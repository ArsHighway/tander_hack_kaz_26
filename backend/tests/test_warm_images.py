from app.catalog import Catalog, Position
from app.search import Candidate
from app.search.candidates import SimilarSku
from app.tools.warm_images import image_jobs


def test_image_jobs_lists_photos_and_category_images_once() -> None:
    catalog = Catalog(
        [
            Position(
                code="A", name="Смесители", okpd2="", characteristics=[], source="t", image_id=10
            ),
            Position(code="B", name="Лейки", okpd2="", characteristics=[], source="t"),
        ],
        "t",
    )
    found = [
        Candidate(
            code="A",
            name="Смесители",
            score=1,
            why=[],
            examples=[SimilarSku(id=1, name="x"), SimilarSku(id=2, name="y")],
        ),
        Candidate(code="B", name="Лейки", score=1, why=[], examples=[SimilarSku(id=2, name="y")]),
        Candidate(code="Z", name="нет в справочнике", score=1, why=[]),
    ]
    assert image_jobs(found, catalog) == ([1, 2], [10])
