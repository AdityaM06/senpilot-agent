from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import TimeoutError as BrowserTimeout
from playwright.async_api import async_playwright

from .delivery import safe_filename
from .models import (
    DocumentRecord,
    DocumentRequest,
    DocumentType,
    DownloadedFile,
    DownloadFailure,
    MatterSnapshot,
    RetrievalResult,
)

URL = "https://uarb.novascotia.ca/fmi/webd/UARB15"
log = logging.getLogger(__name__)

ROW_DATA = """rows => rows.map((row,index) => ({
    dom_index:index,
    fields: [...row.querySelectorAll('.fm-textarea .text')]
        .map(e => e.innerText.trim()).filter(Boolean),
    identifier: row.querySelector('.fm-textarea.hand-cursor .text')?.innerText.trim() || '',
    signature: [...row.querySelectorAll('.fm-textarea')].map(e => e.id).join('|'),
    text: row.innerText,
    position: row.getBoundingClientRect().y
})).sort((a,b)=>a.position-b.position)"""


def record_from_fields(
    fields: list[str], category: DocumentType, identifier: str = ""
) -> DocumentRecord:
    ids = [
        text
        for text in fields
        if re.fullmatch(r"(?:\d{3,12}|[A-Za-z][A-Za-z0-9_-]*\s*-\s*\d[\w().-]*)", text)
    ]
    dates = [text for text in fields if re.match(r"^\d{1,2}/\d{1,2}/\d{4}", text)]
    extensions = [text for text in fields if re.fullmatch(r"\.[a-zA-Z0-9]+", text)]
    if identifier in ids:
        document_id = identifier
    elif len(ids) == 1:
        document_id = ids[0]
    else:
        document_id = (
            "unverified-"
            + hashlib.sha256((str(category) + "|" + "|".join(fields)).encode()).hexdigest()[:16]
        )
    reserved = set(
        [document_id, identifier]
        + dates
        + extensions
        + ["Public", "Confidential", "Protected", "Not Public"]
    )
    titles = [text for text in fields if text not in reserved]
    title = max(titles, key=len, default="Untitled document")
    return DocumentRecord(
        document_id=document_id,
        title=title,
        date=dates[0] if dates else "",
        extension=extensions[0] if extensions else "",
        public=not any(text in {"Confidential", "Protected", "Not Public"} for text in fields),
        categories={category},
    )


def selection_key(record: DocumentRecord) -> tuple:
    try:
        filed = datetime.strptime(record.date.split()[0], "%m/%d/%Y")
    except (ValueError, IndexError):
        filed = datetime.min
    identifier = re.sub(
        r"\d+", lambda match: match.group().zfill(12), record.document_id.casefold()
    )
    return filed, identifier


class UarbClient:
    """Synchronous worker interface over bounded asynchronous browser operations."""

    def __init__(
        self,
        *,
        headless=True,
        browser_channel=None,
        max_documents=10,
        diagnostic_dir: Path | None = None,
    ):
        if not 0 <= max_documents <= 10:
            raise ValueError("Download limit must be between zero and ten")
        self.headless = headless
        self.browser_channel = browser_channel
        self.max_documents = max_documents
        self.diagnostic_dir = diagnostic_dir
        self._loop = None
        self._playwright = None
        self._browser = None
        self._selected_category = None

    def fetch(self, request: DocumentRequest, output_dir: Path) -> RetrievalResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        return self._loop.run_until_complete(
            asyncio.wait_for(
                self._fetch(request, output_dir),
                timeout=600,
            )
        )

    def close(self) -> None:
        if self._loop is not None:
            try:
                self._loop.run_until_complete(self._close())
            finally:
                self._loop.close()
                self._loop = None

    async def _close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _fetch(self, request: DocumentRequest, output_dir: Path) -> RetrievalResult:
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = await self._playwright.chromium.launch(
                headless=self.headless,
                channel=self.browser_channel,
            )
        context = await self._browser.new_context(
            viewport={"width": 1440, "height": 1000},
            accept_downloads=True,
        )
        page = await context.new_page()
        page.set_default_timeout(30_000)
        self._selected_category = None
        await context.tracing.start(screenshots=True, snapshots=True)
        try:
            await self._open_matter(page, request.matter_number)
            metadata = await self._read_metadata(page)
            if metadata.get("Matter No") != request.matter_number:
                raise ValueError("The website returned a different matter number")
            counts = {}
            for category in DocumentType:
                label = await page.get_by_role(
                    "button", name=re.compile(rf"^{re.escape(category.value)}\s*-\s*\d+$")
                ).inner_text()
                counts[category] = int(label.rsplit("-", 1)[1].strip())
            inventories = {}
            errors = {}
            for category, count in counts.items():
                if not count:
                    inventories[category] = []
                    continue
                try:
                    await self._select_category(page, category)
                    rows = await self._collect_inventory(page, category, count)
                    inventories[category] = rows
                    if len(rows) != count or any(
                        row.document_id.startswith("unverified-") for row in rows
                    ):
                        errors[category] = "The complete source-ID inventory could not be verified"
                except Exception as exc:
                    log.warning("Inventory failed for %s: %s", category, type(exc).__name__)
                    inventories[category] = []
                    errors[category] = f"Inventory could not be read ({type(exc).__name__})"
            snapshot = MatterSnapshot(
                matter_number=request.matter_number,
                metadata=metadata,
                source_url=URL,
                category_counts=counts,
                inventories=inventories,
                inventory_errors=errors,
            )
            result = RetrievalResult(request=request, snapshot=snapshot)
            selected = [r for r in inventories[request.document_type] if r.public][
                : self.max_documents
            ]
            if selected:
                await self._select_category(page, request.document_type)
            for record in selected:
                record.categories = {
                    category
                    for category, rows in inventories.items()
                    if any(r.document_id == record.document_id for r in rows)
                }
                last_error = None
                for attempt in range(2):
                    try:
                        result.files.append(await self._download(page, record, output_dir))
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        log.warning(
                            "Download %s attempt %d failed: %s",
                            record.document_id,
                            attempt + 1,
                            type(exc).__name__,
                        )
                        dialog = page.get_by_role("dialog", name="Download Files")
                        if await dialog.is_visible():
                            await dialog.get_by_role("button", name="Close", exact=True).click()
                            await dialog.wait_for(state="hidden")
                if last_error is not None:
                    result.failures.append(
                        DownloadFailure(
                            document_id=record.document_id,
                            reason=f"Download could not be completed ({type(last_error).__name__})",
                        )
                    )
            if (errors or result.failures) and self.diagnostic_dir:
                self.diagnostic_dir.mkdir(parents=True, exist_ok=True)
                stem = f"{request.matter_number}-{time.time_ns()}"
                await page.screenshot(path=str(self.diagnostic_dir / f"{stem}.png"))
                await context.tracing.stop(path=str(self.diagnostic_dir / f"{stem}.zip"))
            else:
                await context.tracing.stop()
            return result
        except BaseException:
            if self.diagnostic_dir:
                self.diagnostic_dir.mkdir(parents=True, exist_ok=True)
                stem = f"{request.matter_number}-{time.time_ns()}"
                try:
                    await page.screenshot(path=str(self.diagnostic_dir / f"{stem}.png"))
                    await context.tracing.stop(path=str(self.diagnostic_dir / f"{stem}.zip"))
                except Exception:
                    log.debug("Could not save browser diagnostics")
            raise
        finally:
            await context.close()

    async def _open_matter(self, page, matter: str) -> None:
        await page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        placeholder = page.get_by_text("eg M01234", exact=True)
        await placeholder.wait_for(timeout=60_000)
        # The placeholder is covered by the field's text layer. Its parent receives clicks.
        await placeholder.locator("..").click()
        editable = page.locator('[contenteditable="true"]')
        await editable.fill(matter)
        await editable.press("Tab")
        await page.get_by_role("button", name="Search", exact=True).last.click()
        await page.get_by_role("button", name=re.compile(r"^Exhibits\s*-\s*\d+$")).wait_for(
            timeout=60_000
        )

    async def _read_metadata(self, page) -> dict[str, str]:
        items = await page.locator(".fm-textarea .text, .fm-text-paragraph").evaluate_all("""
            els => {
                const tab = [...document.querySelectorAll('button')]
                    .find(e => /^Exhibits\\s*-/.test(e.innerText));
                const bottom = tab.getBoundingClientRect().y;
                return els.filter(e => !e.closest('button,[role=grid]'))
                    .map(e => { const r=e.getBoundingClientRect(); return {
                        text:e.innerText.trim().replace(/\\s+/g,' '), x:r.x, y:r.y,
                        bottom:r.bottom, h:r.height,
                        field:e.classList.contains('text')
                    }; }).filter(e => e.h && e.text && e.y < bottom);
            }
        """)
        groups = {
            "Matter No Status": ["Matter No", "Status"],
            "Title - Description": ["Title - Description"],
            "Type Category": ["Type", "Category"],
            "Date Received": ["Date Received"],
            "Decision Date": ["Decision Date"],
            "Date Final Submissions": ["Date Final Submissions"],
            "Outcome": ["Outcome"],
        }
        labels = sorted([item for item in items if item["text"] in groups], key=lambda x: x["x"])
        result = {}
        for index, label in enumerate(labels):
            right = labels[index + 1]["x"] - 24 if index + 1 < len(labels) else 1440
            values = sorted(
                [
                    item
                    for item in items
                    if item["text"] not in groups
                    and label["x"] - 24 <= item["x"] < right
                    and item["y"] >= label["bottom"] - 1
                    and (item["field"] or label["text"] == "Title - Description")
                ],
                key=lambda x: x["y"],
            )
            for name, value in zip(groups[label["text"]], values, strict=False):
                result[name] = value["text"]
        if not result.get("Title - Description"):
            raise ValueError("Matter title could not be read")
        return result

    @staticmethod
    def _rows(page):
        return page.get_by_role("row").filter(
            has=page.get_by_role("button", name=re.compile(r"^GO GET IT$", re.I))
        )

    async def _signature(self, page) -> str:
        rows = await self._rows(page).evaluate_all(ROW_DATA)
        return str([(row["fields"], row["signature"]) for row in rows])

    async def _select_category(self, page, category: DocumentType) -> None:
        if self._selected_category == category:
            await self._scroll_to_top(page)
            return
        previous = await self._signature(page)
        await page.get_by_role(
            "button", name=re.compile(rf"^{re.escape(category.value)}\s*-\s*\d+$")
        ).click()
        await self._rows(page).first.wait_for(timeout=30_000)
        if previous != "[]":
            deadline = time.monotonic() + 25
            while await self._signature(page) == previous:
                if time.monotonic() > deadline:
                    raise TimeoutError("The document tab did not change")
                await page.wait_for_timeout(100)
        self._selected_category = category
        all_types = page.get_by_role("button", name="All Types", exact=True)
        if await all_types.is_visible():
            previous = await self._signature(page)
            await all_types.click()
            await self._wait_for_rows(page, previous)
        await self._scroll_to_top(page)

    async def _scroll_to_top(self, page) -> None:
        scroller = page.locator(".v-grid-scroller-vertical")
        if await scroller.count():
            old = await scroller.evaluate("e=>e.scrollTop")
            if old > 0:
                previous = await self._signature(page)
                await scroller.evaluate("e=>{e.scrollTop=0;}")
                await self._wait_for_rows(page, previous)

    async def _wait_for_rows(self, page, previous: str) -> bool:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if await self._signature(page) != previous:
                return True
            await page.wait_for_timeout(100)
        return False

    async def _advance(self, page) -> bool:
        scroller = page.locator(".v-grid-scroller-vertical")
        if not await scroller.count():
            return False
        previous = await self._signature(page)
        moved = await scroller.evaluate("""e=>{
            const before=e.scrollTop;
            e.scrollTop += Math.max(100,e.clientHeight*0.7);
            return e.scrollTop>before;
        }""")
        if moved:
            await self._wait_for_rows(page, previous)
        return bool(moved)

    async def _collect_inventory(
        self, page, category: DocumentType, expected: int
    ) -> list[DocumentRecord]:
        records = {}
        for _ in range(2):
            await self._scroll_to_top(page)
            for _ in range(250):
                rows = await self._rows(page).evaluate_all(ROW_DATA)
                for row in rows:
                    record = record_from_fields(row["fields"], category, row["identifier"])
                    records.setdefault(record.document_id, record)
                if len(records) >= expected or not await self._advance(page):
                    break
            if len(records) >= expected:
                break
        log.info("%s inventory: %d / %d", category, len(records), expected)
        return sorted(records.values(), key=selection_key, reverse=True)

    async def _find_row(self, page, record: DocumentRecord):
        await self._scroll_to_top(page)
        for _ in range(250):
            if record.document_id.startswith("unverified-"):
                candidates = await self._rows(page).evaluate_all(ROW_DATA)
                category = next(iter(record.categories))
                matches = [
                    candidate
                    for candidate in candidates
                    if record_from_fields(
                        candidate["fields"], category, candidate["identifier"]
                    ).document_id
                    == record.document_id
                ]
                row = self._rows(page).nth(matches[0]["dom_index"]) if matches else None
            else:
                row = (
                    self._rows(page)
                    .filter(has=page.get_by_text(record.document_id, exact=True))
                    .first
                )
            if row is not None and await row.count():
                bounds = await row.bounding_box()
                # Vaadin's role=grid wrapper has zero dimensions; its scroller is the viewport.
                top = await page.get_by_role("grid").evaluate("e=>e.getBoundingClientRect().y")
                height = await page.locator(".v-grid-scroller-vertical").evaluate(
                    "e=>e.clientHeight"
                )
                grid = {"y": top, "height": height or page.viewport_size["height"] - top}
                if (
                    bounds
                    and grid
                    and bounds["y"] >= grid["y"]
                    and (bounds["y"] + bounds["height"] <= grid["y"] + grid["height"])
                ):
                    return row
            if not await self._advance(page):
                break
        raise ValueError("The requested document row could not be located")

    async def _download(self, page, record: DocumentRecord, output_dir: Path) -> DownloadedFile:
        row = await self._find_row(page, record)
        await row.get_by_role("button", name=re.compile(r"^GO GET IT$", re.I)).click()
        dialog = page.get_by_role("dialog", name="Download Files")
        await dialog.wait_for(timeout=30_000)
        buttons = dialog.get_by_role("button").filter(has_not_text=re.compile(r"^Close$"))
        if not record.document_id.startswith("unverified-"):
            expected_filename = re.compile(rf"^{re.escape(record.document_id)}\.", re.I)
            await dialog.get_by_role("button", name=expected_filename).wait_for(timeout=15_000)
            buttons = dialog.get_by_role("button", name=expected_filename)
        if await buttons.count() != 1:
            raise ValueError("This source row has multiple attachment variants")
        async with page.expect_download(timeout=60_000) as event:
            await buttons.click()
        download = await event.value
        if not record.document_id.startswith("unverified-") and (
            Path(download.suggested_filename).stem.casefold() != record.document_id.casefold()
        ):
            await download.cancel()
            raise ValueError("The downloaded filename does not match the source document ID")
        suffix = Path(safe_filename(download.suggested_filename)).suffix
        destination = output_dir / (
            f"{safe_filename(record.document_id)}-{safe_filename(record.title)[:100]}{suffix}"
        )
        try:
            await asyncio.wait_for(download.save_as(destination), timeout=90)
        except (TimeoutError, BrowserTimeout):
            await download.cancel()
            raise
        if await download.failure():
            raise ValueError("The browser reported a failed download")
        size = destination.stat().st_size
        with destination.open("rb") as source:
            header = source.read(1024)
            if not size or header.lstrip().lower().startswith(
                (b"<!doctype html", b"<html", b"<head")
            ):
                raise ValueError("The server returned an empty file or HTML error page")
            if suffix.lower() == ".pdf" and not header.startswith(b"%PDF-"):
                raise ValueError("The downloaded PDF has an invalid signature")
            source.seek(0)
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        await dialog.get_by_role("button", name="Close", exact=True).click()
        await dialog.wait_for(state="hidden")
        log.info("Downloaded source document %s (%d bytes)", record.document_id, size)
        return DownloadedFile(
            document=record,
            path=destination,
            byte_size=size,
            sha256=checksum,
        )
