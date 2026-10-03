import json

from googleapiclient.errors import HttpError

from clipfarm.publishers import youtube


class _FakeResp:
    status = 400
    reason = "Bad Request"


def _http_error(reason: str, message: str) -> HttpError:
    content = json.dumps({"error": {"errors": [{"reason": reason, "message": message}]}}).encode()
    return HttpError(_FakeResp(), content)


def test_upload_limit_exceeded_is_actionable():
    err = youtube._friendly_upload_error(_http_error("uploadLimitExceeded", "The user has exceeded..."))
    text = str(err)
    assert "daily upload limit" in text
    assert "youtube.com/verify" in text  # tells the user how to raise the limit
    assert "HttpError" not in text  # no raw API noise


def test_api_quota_exceeded_message():
    err = youtube._friendly_upload_error(_http_error("quotaExceeded", "Quota exceeded"))
    assert "API quota" in str(err)


def test_unknown_reason_falls_back_to_message():
    err = youtube._friendly_upload_error(_http_error("somethingNew", "A brand new failure"))
    assert "A brand new failure" in str(err)


def test_sanitize_strips_angle_brackets():
    assert youtube._sanitize("Talon <mid> deletes > everyone") == "Talon (mid) deletes ) everyone"


class _FakeCommentService:
    """Captures the commentThreads().insert(...) body the way googleapiclient would send it."""

    def __init__(self):
        self.body = None

    def commentThreads(self):  # noqa: N802 -- mirrors the google client's casing
        return self

    def insert(self, part, body):
        self.body = body
        return self

    def execute(self):
        return {}


def test_engagement_comment_targets_the_new_video():
    service = _FakeCommentService()
    youtube._post_engagement_comment(service, "vid123", "Which champ next? 👇")
    snippet = service.body["snippet"]
    assert snippet["videoId"] == "vid123"
    assert snippet["topLevelComment"]["snippet"]["textOriginal"] == "Which champ next? 👇"


class _CfgSettings:
    def __init__(self, texts=(), single=""):
        from types import SimpleNamespace

        self.youtube = SimpleNamespace(auto_comment_texts=list(texts), auto_comment_text=single)


def test_comment_rotation_is_deterministic_and_spans_the_pool():
    pool = ["Rate this 1-10", "Clean or lucky?", "Which champ next?"]
    s = _CfgSettings(texts=pool)
    picks = {youtube._comment_text_for(s, f"vid{i}") for i in range(40)}
    assert picks == set(pool)  # every option gets used across uploads
    assert youtube._comment_text_for(s, "vidX") == youtube._comment_text_for(s, "vidX")


def test_comment_falls_back_to_single_text_and_empty_disables():
    assert youtube._comment_text_for(_CfgSettings(single="One question?"), "v") == "One question?"
    assert youtube._comment_text_for(_CfgSettings(), "v") == ""
    assert youtube._comment_text_for(_CfgSettings(texts=["  ", ""]), "v") == ""


def test_comments_scope_is_requested_on_reauth():
    # The next `reauth-youtube` must ask for the comments permission, or auto-comment can
    # never activate.
    assert youtube._COMMENTS_SCOPE in youtube.SCOPES


class _FakeThumbnails:
    """thumbnails().set(...).execute() that raises the queued errors, then succeeds."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def thumbnails(self):
        return self

    def set(self, **kwargs):
        return self

    def execute(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return {}


def test_thumbnail_retries_until_new_video_is_registered(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "MediaFileUpload", lambda path: path)
    svc = _FakeThumbnails([_http_error("videoNotFound", "not found"), _http_error("videoNotFound", "not found")])
    waits = []
    assert youtube._set_thumbnail(svc, "VID", "thumb.jpg", sleep=waits.append) is True
    assert svc.calls == 3 and waits == [5, 15]


def test_thumbnail_gives_up_without_raising(monkeypatch):
    monkeypatch.setattr(youtube, "MediaFileUpload", lambda path: path)
    svc = _FakeThumbnails([_http_error("videoNotFound", "x")] * 10)
    assert youtube._set_thumbnail(svc, "VID", "thumb.jpg", sleep=lambda s: None) is False
    assert svc.calls == len(youtube._THUMBNAIL_RETRY_DELAYS) + 1


def test_thumbnail_does_not_retry_other_errors(monkeypatch):
    monkeypatch.setattr(youtube, "MediaFileUpload", lambda path: path)
    svc = _FakeThumbnails([_http_error("forbidden", "verify your account")])
    assert youtube._set_thumbnail(svc, "VID", "thumb.jpg", sleep=lambda s: None) is False
    assert svc.calls == 1
