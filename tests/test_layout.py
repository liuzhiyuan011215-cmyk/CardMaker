"""版面整理：一行一行 → 一段一段（只改空白，不改字）。"""
import pymupdf

from cardmaker.ingest.layout import Line, build_paragraphs, merge_rows, needs_space, tidy_spaces
from cardmaker.ingest.pdf import extract_pdf

WIDTH = 595  # A4 宽度（pt）；正文区 57–539，左右页边距对称


def squeeze(text: str) -> str:
    return "".join(text.split())


def line(text, x0, y0, x1=539.2, size=10.0, bold=False):
    return Line(text, x0, y0, x1, y0 + size * 1.25, size, bold)


def test_needs_space_between_lines():
    assert not needs_space("核心定性标准在于", "审计师")  # 中文直接接
    assert needs_space("正当注意义务（Due", "Professional Care）")  # 英文补空格
    assert needs_space("감사인의 전반적인 목적", "및 감사의 고유한계")  # 韩文按词补空格
    assert not needs_space("认定恒大地产2019-", "2020年")  # 连字符断行：原样接上
    assert not needs_space("https://www.kicpa.or.k", "r")  # 网址断行
    assert not needs_space("报表“", "审计意见")


def test_tidy_spaces_only_around_chinese_punctuation():
    assert tidy_spaces("不能以“ 是否查出舞弊” 的结果") == "不能以“是否查出舞弊”的结果"
    assert tidy_spaces("第一章 总则") == "第一章 总则"  # 汉字之间的空格不动
    assert tidy_spaces("He said “ hello ” twice") == "He said “ hello ” twice"  # 英文不动
    assert tidy_spaces("（ 경영진 ）") == "（ 경영진 ）"  # 韩文旁边不动


def test_paragraphs_headings_bullets_and_references():
    # 坐标取自真实 PDF（Chrome 打印的中文报告）
    lines = [
        line("Q1. 如果发生了审计师未能发现的重大财务舞弊，责任是否在审计师身上？", 57, 49.9, 421.2, 11, bold=True),
        line("审计师是否承担法律与经济责任，不能以“ 是否查出舞弊” 的结果直接倒推，", 57, 72.7),
        line("核心定性标准在于履行了正当注意义务（Due", 57, 89.2),
        line("Professional Care），并全程保持了应有的独立性。", 57, 105.7, 529.2),
        line("• 责任划分原则（경영진 책임 vs 감사인 책임）： 财务报表编制的第一责任人始终是企业管理层", 57, 128.2),
        line("（경영진）。管理层有法定义务建立严密的内部控制并保证数据真实。", 67.5, 144.7),
        line("该意见并非对企业未来经营状况或商业信用的担保。", 67.5, 161.2, 297.3),
        line("• 合理保证 vs 绝对保证（합리적 확신 vs 절대적 확신）：", 57, 182.9, 400.0),
        line("1. 中国证券监督管理委员会行政处罚决定书〔2024〕56号：认定恒大地产2019-", 56.8, 220.0, size=8.5),
        line("2020年虚增收入5641.46亿元。官方网站：https://www.kicpa.or.k", 69.0, 233.5, size=8.5),
        line("r", 69.0, 248.3, 72.4, size=8.0),
    ]
    assert build_paragraphs(lines, WIDTH) == [
        "Q1. 如果发生了审计师未能发现的重大财务舞弊，责任是否在审计师身上？",
        "审计师是否承担法律与经济责任，不能以“是否查出舞弊”的结果直接倒推，核心定性标准在于履行了正当注意义务"
        "（Due Professional Care），并全程保持了应有的独立性。",
        "• 责任划分原则（경영진 책임 vs 감사인 책임）：财务报表编制的第一责任人始终是企业管理层（경영진）。"
        "管理层有法定义务建立严密的内部控制并保证数据真实。该意见并非对企业未来经营状况或商业信用的担保。",
        "• 合理保证 vs 绝对保证（합리적 확신 vs 절대적 확신）：",
        "1. 中国证券监督管理委员会行政处罚决定书〔2024〕56号：认定恒大地产2019-2020年虚增收入5641.46亿元。"
        "官方网站：https://www.kicpa.or.kr",
    ]
    # 底线：把空白都去掉后，一个字都没变
    assert squeeze("".join(build_paragraphs(lines, WIDTH))) == squeeze("".join(ln.text for ln in lines))


def test_short_lines_of_a_word_list_stay_separate():
    # 每行都远没写满（词条），不能连成一段
    lines = [line(text, 57, 60 + i * 16, 57 + 12 * len(text)) for i, text in
             enumerate(["사과 苹果", "배 梨；船；肚子", "포도 葡萄", "수박 西瓜"])]
    assert build_paragraphs(lines, WIDTH) == ["사과 苹果", "배 梨；船；肚子", "포도 葡萄", "수박 西瓜"]


def test_table_cells_on_one_row_are_joined_with_tabs():
    cells = [line("사과", 57, 100, 90), line("苹果；apple", 250, 100, 330),
             line("배", 57, 116, 70), line("梨；pear", 250, 116, 300)]
    assert [ln.text for ln in merge_rows(cells)] == ["사과\t苹果；apple", "배\t梨；pear"]
    assert build_paragraphs(cells, WIDTH) == ["사과\t苹果；apple", "배\t梨；pear"]


def test_two_columns_are_not_merged_across():
    left = [line(f"左栏第{i}行写满了一整行的文字内容", 57, 100 + i * 16, 290) for i in range(6)]
    right = [line(f"右栏第{i}行写满了一整行的文字内容", 305, 100 + i * 16, 539) for i in range(6)]
    paras = build_paragraphs(left + right, WIDTH)
    assert len(paras) == 2 and paras[0].startswith("左栏第0行") and paras[1].startswith("右栏第0行")


def test_extract_pdf_returns_whole_paragraphs(make_pdf):
    korean = ("공유 경제는 자원을 소유하지 않고 서로 빌려 쓰는 경제 활동을 말한다. 최근 스마트폰의 보급과 함께 "
              "빠르게 확산되고 있지만 책임 소재가 불분명하다는 문제도 함께 제기되고 있다. ") * 3
    chinese = "审计师的法定职责是对报表是否在所有重大方面公允反映发表审计意见，该意见并非对企业未来经营状况的担保。" * 3
    path = make_pdf([korean.strip(), chinese])
    doc = extract_pdf(path, ocr_mode="never")
    raw = [p.get_text("text") for p in pymupdf.open(path)]
    assert all(len(r.strip().splitlines()) > 1 for r in raw)  # 原始文字层确实折成了好几行
    assert doc.pages[0].text == korean.strip()  # 韩文按词接回，和原文一字不差
    assert doc.pages[1].text == chinese
