from src.exact_report_generator import ExactReportGenerator


def test_collect_pdf_paths_prefers_full_list_and_removes_duplicates():
    pdf_paths = [
        "/tmp/project_20_cens.pdf",
        "/tmp/project_20_uncens.pdf",
        "/tmp/project_51_cens.pdf",
        "/tmp/project_51_uncens.pdf",
        "/tmp/project_20_cens.pdf",
    ]

    result = ExactReportGenerator._collect_pdf_paths(
        pdf_paths=pdf_paths,
        pdf_20_path="/tmp/project_20_cens.pdf",
        pdf_51_path="/tmp/project_51_cens.pdf",
    )

    assert result == [
        "/tmp/project_20_cens.pdf",
        "/tmp/project_20_uncens.pdf",
        "/tmp/project_51_cens.pdf",
        "/tmp/project_51_uncens.pdf",
    ]


def test_collect_pdf_paths_falls_back_to_legacy_two_paths():
    result = ExactReportGenerator._collect_pdf_paths(
        pdf_20_path="/tmp/project_20_uncens.pdf",
        pdf_51_path="/tmp/project_51_uncens.pdf",
    )

    assert result == [
        "/tmp/project_20_uncens.pdf",
        "/tmp/project_51_uncens.pdf",
    ]
