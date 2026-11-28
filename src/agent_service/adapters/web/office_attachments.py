from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Generator
from contextlib import closing
from io import BytesIO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.formula import ArrayFormula

OFFICE_MIME_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
MAX_OFFICE_BYTES = 10 * 1024 * 1024
MAX_OFFICE_BASE64_CHARS = 4 * ((MAX_OFFICE_BYTES + 2) // 3)
_MAX_UNPACKED_BYTES = 32 * 1024 * 1024
_MAX_ZIP_ENTRIES = 2048
_MAX_SHEETS = 16
_MAX_ROWS = 2000
_MAX_COLUMNS = 64


def _check_package(raw: bytes, extension: str) -> None:
    required_part = {".docx": "word/document.xml", ".xlsx": "xl/workbook.xml"}[
        extension
    ]
    with ZipFile(BytesIO(raw)) as archive:
        entries = archive.infolist()
        if (
            len(entries) > _MAX_ZIP_ENTRIES
            or sum(info.file_size for info in entries) > _MAX_UNPACKED_BYTES
        ):
            raise ValueError("Office 附件解压后的体积或条目数超出上限。")
        names = {info.filename for info in entries}
        if (
            len(names) != len(entries)
            or required_part not in names
            or "[Content_Types].xml" not in names
        ):
            raise ValueError("Office 附件内容与扩展名不匹配，或文件结构无效。")
        if any(
            info.flag_bits & 1 or info.filename.lower().endswith("/vbaproject.bin")
            for info in entries
        ):
            raise ValueError("当前支持未加密且不含宏的 Office 附件。")


def _word_blocks(raw: bytes) -> Generator[str]:
    document = Document(BytesIO(raw))
    table_index = 0
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            if block.text.strip():
                yield block.text
            continue
        table_index += 1
        yield f"[Word 表格 {table_index}]"
        for row in block.rows:
            yield " | ".join(cell.text.replace("\n", " / ") for cell in row.cells)


def _excel_blocks(raw: bytes) -> Generator[str]:
    with (
        closing(
            load_workbook(BytesIO(raw), read_only=True, keep_links=False)
        ) as formulas,
        closing(
            load_workbook(
                BytesIO(raw), read_only=True, data_only=True, keep_links=False
            )
        ) as cached,
    ):
        sheets = [
            sheet for sheet in formulas.worksheets if sheet.sheet_state == "visible"
        ]
        for sheet in sheets[:_MAX_SHEETS]:
            yield f"[工作表：{sheet.title}]"
            max_row = min(sheet.max_row or _MAX_ROWS + 1, _MAX_ROWS + 1)
            max_col = min(sheet.max_column or _MAX_COLUMNS, _MAX_COLUMNS)
            rows = sheet.iter_rows(max_row=max_row, max_col=max_col)
            cached_rows = cached[sheet.title].iter_rows(
                max_row=max_row, max_col=max_col, values_only=True
            )
            with closing(rows), closing(cached_rows):
                for row_number, (row, cached_row) in enumerate(
                    zip(rows, cached_rows), 1
                ):
                    if row_number > _MAX_ROWS:
                        break
                    values: list[str] = []
                    for cell, cached_value in zip(row, cached_row):
                        if cell.value is None:
                            continue
                        value = cell.value
                        if cell.data_type == "f":
                            expression = (
                                value.text
                                if isinstance(value, ArrayFormula)
                                else str(value)
                            )
                            cache_note = (
                                f"缓存值：{cached_value}"
                                if cached_value is not None
                                else "无缓存值"
                            )
                            value = f"{expression}（{cache_note}）"
                        values.append(f"{cell.coordinate} = {value}")
                    if values:
                        yield " | ".join(values)
            if (
                max_row > _MAX_ROWS
                or sheet.max_column is None
                or sheet.max_column > _MAX_COLUMNS
            ):
                yield f"[此工作表的读取范围最多为 {_MAX_ROWS} 行、{_MAX_COLUMNS} 列]"
        if len(sheets) > _MAX_SHEETS:
            yield f"[工作簿读取范围最多为前 {_MAX_SHEETS} 个可见工作表]"


def parse_office_attachment(encoded: str, extension: str, max_chars: int) -> str:
    """从经限额检查的 Office 附件提取正文，额外保留一个字符用于标记截断。"""
    if len(encoded) > MAX_OFFICE_BASE64_CHARS:
        raise ValueError("Office 附件超过单文件 10 MB 上限。")
    try:
        raw = b64decode(encoded, validate=True)
    except (Base64Error, ValueError) as exc:
        raise ValueError("Office 附件编码无效。") from exc
    if not raw:
        raise ValueError("Office 附件为空。")
    if len(raw) > MAX_OFFICE_BYTES:
        raise ValueError("Office 附件超过单文件 10 MB 上限。")

    try:
        _check_package(raw, extension)
        blocks = _word_blocks(raw) if extension == ".docx" else _excel_blocks(raw)
        lines: list[str] = []
        remaining = max_chars + 1
        with closing(blocks):
            for line in blocks:
                if lines:
                    remaining -= 1
                lines.append(line[:remaining])
                remaining -= len(lines[-1])
                if remaining <= 0:
                    break
    except (
        BadZipFile,
        DefusedXmlException,
        InvalidFileException,
        PackageNotFoundError,
        ParseError,
        XMLSyntaxError,
        KeyError,
        IndexError,
        OSError,
        TypeError,
    ) as exc:
        raise ValueError("无法解析 Office 附件，请确认文件格式正确且未损坏。") from exc
    if not lines:
        raise ValueError("Office 附件没有可读取的正文或可见工作表。")
    return "\n".join(lines)
