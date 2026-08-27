"""API contract integration tests (sample repo for scanner tests)."""


def test_item_contract_fields_are_unique(expected_item_fields: tuple[str, ...]) -> None:
    assert len(set(expected_item_fields)) == len(expected_item_fields)
    assert "id" in expected_item_fields
