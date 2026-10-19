"""Validate field coverage, preserve existing CSVs, render only this newly created guide."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import shutil
import zipfile

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results' / 'csv_output_guide_20261016'


def main():
    manifest = json.loads((OUT / 'guide_manifest.json').read_text(encoding='utf-8'))
    path = OUT / (manifest['name'] + '.docx')
    document = Document(path)
    table_text = '\n'.join(c.text for t in document.tables for r in t.rows for c in r.cells)
    body_text = '\n'.join(p.text for p in document.paragraphs)
    with (OUT / 'csv_field_dictionary.csv').open(encoding='utf-8-sig', newline='') as stream:
        dictionary = list(csv.DictReader(stream))
    assert len(dictionary) == manifest['schema_column_definitions']
    for row in dictionary:
        assert row['field'] in table_text, ('Missing field in Word', row)
        assert row['definition'].strip() and row['unit'].strip()
    for name in manifest['covered_filenames']:
        assert name in body_text or name in table_text, ('Missing filename', name)
    assert len(document.tables) == manifest['tables']
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
    # Full-file hashing of approximately 4.9 GB is unnecessary for this read-only job;
    # check all original file existence, size, mtime, and headers instead.
    import hashlib
    for old in manifest['original_files']:
        source = ROOT / old['path']
        stat = source.stat()
        assert stat.st_size == old['bytes'] and stat.st_mtime_ns == old['mtime_ns'], source
        with source.open(encoding='utf-8-sig', newline='') as stream:
            header = next(csv.reader(stream))
        actual = hashlib.sha256(json.dumps(header, ensure_ascii=False).encode()).hexdigest()
        assert actual == old['header_sha256'], source
    import win32com.client
    word = opened = None
    try:
        word = win32com.client.DispatchEx('Word.Application')
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        opened = word.Documents.Open(str(path), ReadOnly=False, AddToRecentFiles=False, Visible=False)
        opened.Fields.Update()
        for toc in opened.TablesOfContents:
            toc.Update()
        opened.Repaginate()
        for toc in opened.TablesOfContents:
            toc.UpdatePageNumbers()
        opened.Save()
        pdf = OUT / (path.stem + '.pdf')
        opened.ExportAsFixedFormat(str(pdf), 17)
    finally:
        if opened is not None:
            opened.Close(False)
        if word is not None:
            word.Quit()
    import pymupdf
    rendered = pymupdf.open(pdf)
    empty = []
    overflow = []
    texts = []
    previews = OUT / 'page_previews'
    previews.mkdir(exist_ok=True)
    selected = {0, 1, 2, 3, len(rendered) - 1}
    selected.update(range(4, len(rendered), 9))
    for index, page in enumerate(rendered):
        content = page.get_text()
        texts.append(content)
        if len(content.strip()) < 25:
            empty.append(index + 1)
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', []):
                for span in line.get('spans', []):
                    x0, _, x1, _ = span['bbox']
                    if x0 < 8 or x1 > page.rect.width - 8:
                        overflow.append(dict(page=index + 1, text=span['text'], bbox=list(span['bbox'])))
        if index in selected:
            page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(str(previews / f'page_{index+1:03}.png'))
    (OUT / 'rendered_text.txt').write_text('\n\n'.join(texts), encoding='utf-8')
    assert not empty, ('Unexpected empty pages', empty)
    assert not overflow, ('Horizontal text overflow', overflow[:8])
    status = dict(field_coverage='PASS', csv_filename_types=manifest['filename_types'],
        schemas=manifest['schemas'], schema_column_definitions=len(dictionary),
        original_csv_files_metadata_and_headers_unchanged='PASS',
        original_csv_files_checked=manifest['original_csv_files'],
        docx_structure='PASS', word_pdf_render='PASS', toc_updated='PASS',
        pages=len(rendered), tables=len(document.tables), empty_pages=empty, horizontal_overflow=overflow)
    try:
        shutil.copy2(path, ROOT / path.name)
        shutil.copy2(pdf, ROOT / pdf.name)
        status['project_root_copy'] = 'PASS'
    except PermissionError:
        status['project_root_copy'] = 'LOCKED; canonical result files remain available'
    (OUT / 'guide_validation.json').write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(status, ensure_ascii=True))


if __name__ == '__main__':
    main()
