"""Finding a topic's video and the link to stream or download it."""

import re

import httpx

from .app import RUN
from .client import _client
from .courses import _resolve, _toc_index
from .files import _safe
from .download import _course


def _topic_parents(modules, topic_id: int, parents=()) -> tuple | None:
    """The unit folders a topic is saved under, as download_course names them."""
    for module in modules or []:
        path = parents + (_safe(module.get("Title", "unit")),)
        if any(t["TopicId"] == topic_id for t in module.get("Topics") or []):
            return path
        found = _topic_parents(module.get("Modules"), topic_id, path)
        if found:
            return found
    return None


def _video_link(ou: int, topic, disposition: str) -> str:
    """A short-lived signed link to a media-service video's file.

    Videos live in Brightspace's media service, not the course's file area,
    and the session does not reach it. The player gets in the way done here:
    the topic's page carries an anti-forgery token, which buys a short-lived
    bearer token from DTU Learn, which buys a signed link from the media
    service. The service only answers with the topic named as context; the
    same request without it is refused (403)."""
    parts = (topic.get("Url") or "").split(":")
    if parts[:3] != ["d2l", "brightspace", "content"] or len(parts) < 7:
        raise RuntimeError(f"'{topic.get('Title')}' is not in Brightspace's media service.")
    region, tenant = parts[3], parts[4]
    content, _, revision = parts[6].partition("/")
    with _client() as c:
        page = c.get(f"/d2l/le/lessons/{ou}/topics/{topic['TopicId']}", headers={"Accept": "text/html"})
        xsrf = re.search(r"XSRF\.Token','([^']+)'", page.text)
        if not xsrf:
            raise RuntimeError(
                "DTU Learn handed out no page token, so the session has probably "
                f"expired. Run '{RUN} login' again."
            )
        r = c.post("/d2l/lp/auth/oauth2/token", data={"scope": "*:*:*"},
                   headers={"X-Csrf-Token": xsrf.group(1)})
        r.raise_for_status()
        token = r.json()["access_token"]
    r = httpx.get(
        f"https://api.{region}.content-service.brightspace.com/api/{tenant}/content/{content}"
        f"/revisions/{revision or 'latest'}/resources/transcodes/signed-url",
        params={"disposition": disposition, "contextType": "topic", "contextId": f"{topic['TopicId']}:{ou}"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.status_code in (401, 403):
        raise RuntimeError(f"The media service refused '{topic.get('Title')}' (HTTP {r.status_code}).")
    r.raise_for_status()
    try:
        answer = r.json()
    except ValueError:
        return r.text.strip().strip('"')
    if isinstance(answer, dict):
        answer = answer.get("value") or answer.get("url") or answer.get("Url") or answer.get("signedUrl")
    if not isinstance(answer, str) or not answer.startswith("https://"):
        raise RuntimeError("The media service answered without a link.")
    return answer


def _video_topic(org_unit_id: int | str, topic_id: int) -> tuple:
    ou = _resolve(org_unit_id)
    copy = _course(ou)
    topic = _toc_index(copy["modules"])[0].get(topic_id)
    if topic is None or topic.get("TypeIdentifier") != "ContentService":
        raise RuntimeError(f"Topic {topic_id} is not a video in that course. Run course_contents.")
    return ou, copy, topic
