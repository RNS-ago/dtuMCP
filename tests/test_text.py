"""Tests for dtumcp.text."""
import tempfile
import zipfile
from pathlib import Path

from _package import mod


def test_ooxml_text():
    with tempfile.TemporaryDirectory() as tmp:
        docx = Path(tmp) / "a.docx"
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr(
                "word/document.xml",
                "<w:document><w:body><w:p><w:r><w:t>Cover &amp; Page</w:t></w:r></w:p>"
                "<w:p><w:r><w:t>Second line</w:t></w:r></w:p></w:body></w:document>",
            )
            z.writestr("docProps/app.xml", "<Properties><Company>ignored</Company></Properties>")
        word = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        # Paragraphs become lines, entities are decoded, other members are ignored.
        assert mod._ooxml_text(docx, word) == "Cover & Page\nSecond line", mod._ooxml_text(docx, word)
        # A content type that is not OOXML is not this function's business.
        assert mod._ooxml_text(docx, "application/pdf") is None
        # Something claiming to be OOXML but not a zip fails softly.
        plain = Path(tmp) / "b.docx"
        plain.write_bytes(b"not a zip")
        assert mod._ooxml_text(plain, word) is None


def test_html_text():
    # Script and style contents are dropped; block tags become line breaks.
    assert mod._html_text(
        "<html><head><style>p{color:red}</style></head>"
        "<body><h2>Deadlines</h2><p>Task 1</p><p>30 September</p>"
        "<script>var x=1</script></body></html>"
    ) == "Deadlines\nTask 1\n30 September"
    assert mod._html_text("<p>  spaced   out  </p>") == "spaced out"
    assert mod._html_text("") == ""


def test_pdf_text():
    # Anything pdftotext cannot read yields None, so get_file hands over the file.
    assert mod._pdf_text(Path("/nonexistent/nope.pdf")) is None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as empty:
        assert mod._pdf_text(Path(empty.name)) is None


def test_workbook():
    # A workbook reads a sheet a page, a row a line, cells by address: text in
    # quotes, a formula after its result, a shared one by the cell it is written
    # in, dates as dates, phonetic guides left out.
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = Path(tmp) / "calc.xlsx"
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        with zipfile.ZipFile(xlsx, "w") as z:
            z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="Calc" r:id="r1"/>'
                       '<sheet name="Data" r:id="r2"/><sheet name="Pictures" r:id="r3" state="hidden"/>'
                       '<sheet name="Tail" r:id="r4"/></sheets></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       + "".join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, 5)) + "</Relationships>")
            z.writestr("xl/sharedStrings.xml", f'<sst {ns}><si><t>Diesel</t></si>'
                       '<si><r><t>Tō</t></r><r><t>kyo</t></r><rPh><t>とうきょう</t></rPh></si></sst>')
            z.writestr("xl/styles.xml", f'<styleSheet {ns}><numFmts><numFmt numFmtId="164" formatCode="dd/mm/yyyy"/>'
                       '<numFmt numFmtId="165" formatCode="0.00&quot; kg&quot;"/></numFmts>'
                       '<cellXfs><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/><xf numFmtId="14"/></cellXfs></styleSheet>')
            z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><dimension ref="A1:D4"/><sheetData>'
                       '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>0.52000000000000002</v></c>'
                       '<c r="C1" s="2"><v>3</v></c><c r="D1"><f t="shared" ref="D1:D3" si="0">B1*C1</f><v>1.56</v></c></row>'
                       '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="D2"><f t="shared" si="0"/><v>0</v></c></row>'
                       '<row r="3"><c r="A3" s="1"><v>45000</v></c><c r="B3" s="3"><v>45000.5</v></c><c r="C3" t="b"><v>1</v></c>'
                       '<c r="D3" t="e"><f>1/0</f><v>#DIV/0!</v></c></row>'
                       '<row r="4"><c r="A4" t="inlineStr"><is><t>say "hi"</t></is></c><c r="B4" t="str"><f>A1&amp;"!"</f><v>Diesel!</v></c><c r="C4"/></row>'
                       '</sheetData></worksheet>')
            z.writestr("xl/worksheets/sheet2.xml", f'<worksheet {ns}><dimension ref="A1:A300"/><sheetData>'
                       + "".join(f'<row r="{r}"><c r="A{r}"><v>{r}</v></c></row>' for r in range(1, 301)) + "</sheetData></worksheet>")
            z.writestr("xl/worksheets/sheet3.xml", f'<worksheet {ns}><dimension ref="A1"/><sheetData/></worksheet>')
            z.writestr("xl/worksheets/sheet4.xml", f'<worksheet {ns}><sheetData><row r="1"><c r="B1"><v>7</v></c></row></sheetData></worksheet>')
        got = mod._sheet_text(xlsx, "calc.xlsx")
        for want in ['  1. "Calc" A1:D4', '  3. "Pictures" A1 (hidden)', '  4. "Tail" empty', '[Sheet 1: "Calc"]',
                     '1: A "Diesel" · B 0.52 · C 3 · D 1.56 = B1*C1', '2: A "Tōkyo" · D 0 = as in D1',
                     "3: A 2023-03-15 · B 2023-03-15 12:00 · C TRUE · D #DIV/0! = 1/0",
                     '4: A "say \\"hi\\"" · B "Diesel!" = A1&"!"', "300: A 300", "(No cells with anything in them",
                     '[Sheet 4: "Tail"]', "1: B 7"]:
            assert want in got, (want, got)
        assert "とうきょう" not in got and "C4" not in got
        assert mod._sheet_text(xlsx, "calc.xlsx", (2, 2), first_row=299).split("[Sheet 2: \"Data\"]\n")[1] == \
            "299: A 299\n300: A 300\n\n[Sheets 3 to 4 follow: ask first_page=3.]"
        assert mod._sheet_pages(xlsx)[0] == 'Calc\nA1 "Diesel" · B1 0.52 · C1 3 · D1 1.56\nA2 "Tōkyo" · D2 0\n' \
            'A3 2023-03-15 · B3 2023-03-15 12:00 · C3 TRUE · D3 #DIV/0!\nA4 "say \\"hi\\"" · B4 "Diesel!"'
        saved_limit = mod.TEXT_LIMIT
        mod.TEXT_LIMIT = 400  # the long sheet 2 is named, and the small ones after it still shown
        got = mod._sheet_text(xlsx, "calc.xlsx")
        assert '[Sheet 2: "Data"]' not in got and '[Sheet 4: "Tail"]' in got and "sheet 2. Ask first_page=2 for it." in got, got
        got = mod._sheet_text(xlsx, "calc.xlsx", (2, 0))  # asked for first, it is cut by rows
        assert "[Sheet 2 goes on: ask first_page=2, first_row=" in got and "Sheets 3" not in got, got
        mod.TEXT_LIMIT = saved_limit
        # find: the rows with every word, at a word's start, after a sheet's first
        # rows for its labels; a count of all that match; where to go on.
        saved_labels, mod.LABEL_ROWS = mod.LABEL_ROWS, 1
        got = mod._sheet_text(xlsx, "calc.xlsx", find="DIESEL")
        assert ('[Sheet 1: "Calc": 2 of its 4 rows match]\n1: A "Diesel"' in got and '\n4: A "say' in got
                and "\n2: A" not in got and "(No row of sheets 2, 3, 4 has every word.)" in got), got
        got = mod._sheet_text(xlsx, "calc.xlsx", (2, 2), find="29")
        assert "11 of its 300 rows match" in got and "\n29: A 29\n290: A 290" in got and "\n129:" not in got, got
        assert "(No row of sheets 1, 2, 3, 4 has every word.)" in mod._sheet_text(xlsx, "calc.xlsx", find="kyo")  # inside Tōkyo
        assert '1 of its 4 rows match]\n1: A "Diesel"' in mod._sheet_text(xlsx, "calc.xlsx", find="b1")  # in the formula B1*C1
        mod.TEXT_LIMIT = 80
        got = mod._sheet_text(xlsx, "calc.xlsx", (2, 2), find="29")
        assert "11 of its 300 rows match" in got and '[More rows match in sheet 2: ask first_page=2, first_row=' in got \
            and 'find="29" for the rest.]' in got, got
        mod.TEXT_LIMIT, mod.LABEL_ROWS = saved_limit, saved_labels
        for bad in [(0, 0), (5, 0)]:
            try:
                mod._sheet_text(xlsx, "calc.xlsx", bad)
                raise AssertionError(bad)
            except RuntimeError as e:
                assert "has sheets 1 to 4" in str(e), e
        (Path(tmp) / "broken.xlsx").write_bytes(b"not a zip")
        assert mod._sheet_text(Path(tmp) / "broken.xlsx", "broken.xlsx") is None and mod._sheet_pages(Path(tmp) / "broken.xlsx") == []
        # Strict Open XML reads alike: its own namespaces, parts found through
        # the package's relations (here not under xl/), and dates written out.
        strict = Path(tmp) / "strict.xlsx"
        ns = 'xmlns="http://purl.oclc.org/ooxml/spreadsheetml/main"'
        rel = 'xmlns:r="http://purl.oclc.org/ooxml/officeDocument/relationships"'
        kind = "http://purl.oclc.org/ooxml/officeDocument/relationships/"
        package = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        with zipfile.ZipFile(strict, "w") as z:
            z.writestr("_rels/.rels", f'{package}<Relationship Id="a" Type="{kind}officeDocument" Target="book/main.xml"/></Relationships>')
            z.writestr("book/main.xml", f'<workbook {ns} {rel}><sheets><sheet name="Strict" r:id="s1"/></sheets></workbook>')
            z.writestr("book/_rels/main.xml.rels", f'{package}<Relationship Id="s1" Type="{kind}worksheet" Target="sheets/one.xml"/>'
                       f'<Relationship Id="s2" Type="{kind}sharedStrings" Target="words.xml"/></Relationships>')
            z.writestr("book/words.xml", f'<sst {ns}><si><t>Diesel</t></si></sst>')
            z.writestr("book/sheets/one.xml", f'<worksheet {ns}><dimension ref="A1:C1"/><sheetData><row r="1">'
                       '<c r="A1" t="s"><v>0</v></c><c r="B1"><v>2.5</v></c><c r="C1" t="d"><v>2023-03-15T00:00:00</v></c>'
                       '<c r="D1" t="d"><v>2023-03-15T12:30:00Z</v></c></row>'
                       # A break typed in a cell or a formula stays on the row's line.
                       '<row r="2"><c r="A2" t="inlineStr"><is><t>Total\ncost</t></is></c>'
                       '<c r="B2"><f>SUM(B1,\r\n  B1)</f><v>5</v></c></row></sheetData></worksheet>')
        got = mod._sheet_text(strict, "strict.xlsx")
        assert '  1. "Strict" A1:C1' in got and '1: A "Diesel" · B 2.5 · C 2023-03-15 · D 2023-03-15 12:30' in got, got
        assert '\n2: A "Total cost" · B 5 = SUM(B1, B1)\n' in got + "\n", got
        assert mod._sheet_pages(strict) == ['Strict\nA1 "Diesel" · B1 2.5 · C1 2023-03-15 · D1 2023-03-15 12:30\n'
                                            'A2 "Total cost" · B2 5']  # searched too
        assert "2: A \"Total cost\"" in mod._sheet_text(strict, "strict.xlsx", find="cost")
