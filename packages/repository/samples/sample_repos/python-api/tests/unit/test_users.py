"""User fixture unit tests (sample repo for scanner tests)."""


def test_sample_user_is_a_member(sample_user: dict[str, str]) -> None:
    assert sample_user["role"] == "member"
    assert sample_user["email"].endswith("@example.com")
