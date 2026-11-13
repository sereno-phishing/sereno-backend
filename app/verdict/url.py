"""URL validation and the cache key (decision I-46): the SHA-256 of the URL
normalized with the same RFC 3986 normalizer the model was trained with.

The normalizer lives in the sereno-model package, which the backend needs
anyway to load the model artifact; this module imports only its
standard-library part, so no ML dependency is loaded here."""
from hashlib import sha256

try:
    from sereno_model.data.normalize import InvalidUrlError, parse_url
except ImportError as error:  # pragma: no cover - depends on the environment
    raise ImportError(
        "The sereno-model package is not installed. From sereno-backend/, run "
        "'.venv/Scripts/python.exe -m pip install --no-deps -e ../sereno-model'.") from error


class InvalidUrl(Exception):
    pass


def parse(url):
    """Normalized components of an http(s) URL with a host; anything else is InvalidUrl."""
    if not isinstance(url, str):
        raise InvalidUrl
    try:
        return parse_url(url)
    except InvalidUrlError as error:
        raise InvalidUrl from error


def normalize(url):
    return parse(url).normalized


def site_domain(host):
    """The normalized host without a leading "www.": the domain policies,
    invalidations, history and metrics are expressed in."""
    return host[4:] if host.startswith("www.") else host


def cache_key(normalized_url):
    """Hex SHA-256 of the normalized URL: the cache never stores the URL itself (RNF-05)."""
    return sha256(normalized_url.encode("utf-8")).hexdigest()
