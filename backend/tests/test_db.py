from concurrent.futures import ThreadPoolExecutor

from app.db import Repository


def test_concurrent_workers_claim_a_run_only_once(tmp_path):
    repo = Repository(tmp_path / "state.db")
    run, _ = repo.create_run({"query": "event"})
    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = list(executor.map(lambda _: repo.claim_next_run(), range(4)))
    assert [value["id"] for value in claimed if value] == [run["id"]]


def test_concurrent_repeated_submission_creates_one_run(tmp_path):
    repo = Repository(tmp_path / "state.db")
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: repo.create_run({"query": "event"}, "same", "hash"), range(4)))
    assert len({run["id"] for run, _ in results}) == 1
    assert sum(created for _, created in results) == 1
