import json
import os

from crawler.state import write_json_atomic


def test_json_written_whole_and_mode_kept(tmp_path):
    p = tmp_path / "index.json"
    p.write_text("{}")
    before = os.stat(p).st_mode & 0o777
    write_json_atomic(p, {"pages": [1, 2]}, indent=2)
    assert json.loads(p.read_text()) == {"pages": [1, 2]}
    assert os.stat(p).st_mode & 0o777 == before
    assert not [f for f in tmp_path.iterdir() if f.name.endswith(".tmp")]


def test_new_file_gets_the_permissions_of_an_ordinary_write(tmp_path):
    p = tmp_path / "pdf_link_texts.json"
    write_json_atomic(p, {"a": "b"})
    q = tmp_path / "plain.json"
    q.write_text("{}")
    assert os.stat(p).st_mode & 0o777 == os.stat(q).st_mode & 0o777
