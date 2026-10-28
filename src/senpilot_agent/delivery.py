from __future__ import annotations

import json
import re
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .models import (
    DeliveryPackage,
    DocumentType,
    DownloadFailure,
    InboundEmail,
    RetrievalResult,
    utc_now,
)


def safe_filename(value: str) -> str:
    name = re.sub(r"[^\w.() -]+", "_", value.replace("\\", "/").split("/")[-1])
    return name.strip(" .")[:160] or "document"


def _archive_name(file) -> str:
    name = file.path.name
    if not name.startswith(file.document.document_id + "-"):
        name = f"{file.document.document_id}-{name}"
    return safe_filename(name)


def build_package(
    result: RetrievalResult, output_dir: Path, attachment_budget: int
) -> DeliveryPackage:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{result.request.matter_number}-documents.zip"
    included = []
    omitted = []

    def write_archive() -> None:
        manifest = {
            "request": result.request.model_dump(mode="json"),
            "source_url": result.snapshot.source_url,
            "collected_at": result.snapshot.collected_at.isoformat(),
            "metadata": result.snapshot.metadata,
            "category_counts": result.snapshot.category_counts,
            "unique_total": result.snapshot.unique_total,
            "counting": "Deduplicated by listed source identifier, not by file contents.",
            "inventory_errors": result.snapshot.inventory_errors,
            "inventory": {
                category: [record.model_dump(mode="json") for record in records]
                for category, records in result.snapshot.inventories.items()
            },
            "documents": [
                file.model_dump(mode="json", exclude={"path"}) | {"filename": _archive_name(file)}
                for file in included
            ],
            "failures": [f.model_dump() for f in result.failures],
            "omissions": [f.model_dump() for f in omitted],
        }
        with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
            for file in included:
                archive.write(file.path, _archive_name(file))

    for file in result.files:
        included.append(file)
        write_archive()
        if destination.stat().st_size > attachment_budget:
            included.pop()
            omitted.append(
                DownloadFailure(
                    document_id=file.document.document_id,
                    reason="Does not fit the email attachment budget",
                )
            )
    write_archive()
    if destination.stat().st_size > attachment_budget:
        raise ValueError("The manifest alone exceeds the attachment budget")
    return DeliveryPackage(
        zip_path=destination,
        included_ids=[f.document.document_id for f in included],
        omitted=omitted,
    )


def compose_reply(
    incoming: InboundEmail,
    agent_email: str,
    outbound_id: str,
    body: str,
    package: DeliveryPackage | None = None,
) -> EmailMessage:
    message = EmailMessage(policy=policy.SMTP)
    message["From"] = agent_email
    message["To"] = incoming.sender
    subject = re.sub(r"[\r\n]+", " ", incoming.subject)[:500]
    message["Subject"] = "Re: " + re.sub(r"^(Re:\s*)+", "", subject, flags=re.I)
    message["Message-ID"] = outbound_id
    message["Date"] = format_datetime(utc_now())
    message["Auto-Submitted"] = "auto-replied"
    if re.fullmatch(r"<[^<>\s]+@[^<>\s]+>", incoming.message_id):
        message["In-Reply-To"] = incoming.message_id
        message["References"] = incoming.message_id
    message.set_content(body)
    if package and package.zip_path:
        message.add_attachment(
            package.zip_path.read_bytes(),
            maintype="application",
            subtype="zip",
            filename=package.zip_path.name,
        )
    return message


def result_body(result: RetrievalResult, package: DeliveryPackage) -> str:
    snapshot = result.snapshot
    lines = [f"Matter {snapshot.matter_number}", ""]
    lines.extend(f"{label}: {value}" for label, value in snapshot.metadata.items() if value)
    lines.extend(["", "Files listed by category (categories may overlap):"])
    lines.extend(f"- {category}: {snapshot.category_counts[category]}" for category in DocumentType)
    lines.append(
        f"Total unique source files: {snapshot.unique_total} (by listed identifier)."
        if snapshot.unique_total is not None
        else "Total unique source files: not verified."
    )
    if any(not record.public for rows in snapshot.inventories.values() for record in rows):
        lines.append("Counts include restricted entries; only public files are downloaded.")
    available = snapshot.category_counts[result.request.document_type]
    lines.extend(
        [
            "",
            f"Requested category: {result.request.document_type} ({available} listed).",
            f"Attached {len(package.included_ids)} source documents plus manifest.json in a ZIP.",
        ]
    )
    restricted = sum(
        not record.public for record in snapshot.inventories[result.request.document_type]
    )
    if restricted:
        lines.append(f"Skipped {restricted} restricted entries in the requested category.")
    for failure in [*result.failures, *package.omitted]:
        lines.append(f"- Not attached: {failure.document_id}: {failure.reason}")
    for category, error in snapshot.inventory_errors.items():
        lines.append(f"- Inventory limitation for {category}: {error}")
    lines.extend(
        ["", f"Source: {snapshot.source_url}", f"Collected at: {snapshot.collected_at.isoformat()}"]
    )
    return "\n".join(lines)
