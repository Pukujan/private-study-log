"""Tests for the F2 rule-pack of the defensive security detector extension.

Six new classes targeting five previously-requested CWEs (AST-only, no
execution, no network):

    credential_in_uri, default_arg_credential   CWE-798 hard-coded credentials
    unsafe_deserialization                      CWE-502 unsafe deserialization
    ssrf_taint                                  CWE-918 SSRF
    xml_entity_expansion                        CWE-776 XML entity expansion
    resource_exhaustion                         CWE-400 resource exhaustion

Every rule is exercised both ways: the vulnerable form must fire, the fixed
form must stay silent. The zero-false-positive promotion invariant on the
original Stage-2C fixtures is re-asserted here for the F2 classes.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.objective_security.detector import detect_classes  # noqa: E402
from evals.objective_security.detector_ext import (  # noqa: E402
    F2_CLASSES,
    detect_ext,
    detect_ext_findings,
)
from evals.objective_security.fixtures import fixtures  # noqa: E402


# --- CWE-798: hard-coded credentials ------------------------------------

def test_credential_in_uri_flagged():
    assert "credential_in_uri" in detect_ext(
        "DB_URL = 'postgresql://app:hunter2@db.internal/prod'\n")


def test_templated_uri_is_clean():
    # placeholder password = the secure form (filled at runtime)
    assert "credential_in_uri" not in detect_ext(
        "DB_URL = 'postgresql://app:{pw}@db.internal/prod'.format(pw=pw)\n")
    assert "credential_in_uri" not in detect_ext(
        "DB_URL = 'postgresql://app:$DB_PASSWORD@db.internal/prod'\n")


def test_plain_url_with_port_is_clean():
    assert "credential_in_uri" not in detect_ext(
        "URL = 'http://api.internal:8080/status'\n")


def test_default_arg_credential_flagged():
    assert "default_arg_credential" in detect_ext(
        "def connect(host, password='hunter2'):\n    return _open(host, password)\n")
    assert "default_arg_credential" in detect_ext(
        "def connect(host, *, api_key='sk-live-123'):\n    return _open(host, api_key)\n")


def test_default_arg_none_is_clean():
    assert "default_arg_credential" not in detect_ext(
        "def connect(host, password=None):\n    return _open(host, password)\n")


# --- CWE-502: unsafe deserialization ------------------------------------

def test_yaml_unsafe_and_full_load_flagged():
    assert "unsafe_deserialization" in detect_ext(
        "import yaml\ndef f(t):\n    return yaml.unsafe_load(t)\n")
    assert "unsafe_deserialization" in detect_ext(
        "import yaml\ndef f(t):\n    return yaml.full_load(t)\n")


def test_dill_and_jsonpickle_flagged():
    assert "unsafe_deserialization" in detect_ext(
        "import dill\ndef f(b):\n    return dill.loads(b)\n")
    assert "unsafe_deserialization" in detect_ext(
        "import jsonpickle\ndef f(s):\n    return jsonpickle.decode(s)\n")


def test_unpickler_instance_flagged():
    assert "unsafe_deserialization" in detect_ext(
        "import pickle\ndef f(fh):\n    return pickle.Unpickler(fh).load()\n")
    assert "unsafe_deserialization" in detect_ext(
        "import pickle\ndef f(fh):\n    u = pickle.Unpickler(fh)\n    return u.load()\n")


def test_torch_load_weights_only_false_flagged():
    assert "unsafe_deserialization" in detect_ext(
        "import torch\ndef f(p):\n    return torch.load(p, weights_only=False)\n")
    assert "unsafe_deserialization" not in detect_ext(
        "import torch\ndef f(p):\n    return torch.load(p, weights_only=True)\n")


def test_safe_deserialization_is_clean():
    assert "unsafe_deserialization" not in detect_ext(
        "import yaml\ndef f(t):\n    return yaml.safe_load(t)\n")
    assert "unsafe_deserialization" not in detect_ext(
        "import json\ndef f(b):\n    return json.loads(b)\n")


# --- CWE-918: SSRF -------------------------------------------------------

def test_ssrf_taint_bare_name_flagged():
    assert "ssrf_taint" in detect_ext(
        "import requests\ndef fetch(request):\n"
        "    url = request.args.get('u')\n"
        "    return requests.get(url)\n")


def test_ssrf_taint_fstring_url_flagged():
    assert "ssrf_taint" in detect_ext(
        "import requests\ndef fetch(request):\n"
        "    host = request.args.get('h')\n"
        "    return requests.get(f'http://{host}/status')\n")


def test_ssrf_taint_httpx_flagged():
    assert "ssrf_taint" in detect_ext(
        "import httpx\ndef fetch(request):\n"
        "    url = request.args.get('u')\n"
        "    return httpx.get(url)\n")


def test_ssrf_validated_is_clean():
    assert "ssrf_taint" not in detect_ext(
        "import requests\nfrom urllib.parse import urlparse\n"
        "ALLOWED = {'api.internal'}\n"
        "def fetch(request):\n"
        "    url = request.args.get('u')\n"
        "    if urlparse(url).hostname not in ALLOWED:\n"
        "        raise ValueError('blocked')\n"
        "    return requests.get(url)\n")


def test_ssrf_untainted_param_is_clean():
    # a plain function parameter is not request taint (base ssrf rule's turf)
    assert "ssrf_taint" not in detect_ext(
        "import requests\ndef fetch(url):\n    return requests.get(url)\n")


# --- CWE-776: XML entity expansion ---------------------------------------

def test_lxml_huge_tree_flagged():
    assert "xml_entity_expansion" in detect_ext(
        "from lxml import etree\n"
        "parser = etree.XMLParser(resolve_entities=False, huge_tree=True)\n")


def test_sax_external_entities_enabled_flagged():
    assert "xml_entity_expansion" in detect_ext(
        "import xml.sax\nfrom xml.sax.handler import feature_external_ges\n"
        "def parse(src):\n"
        "    p = xml.sax.make_parser()\n"
        "    p.setFeature(feature_external_ges, True)\n"
        "    p.parse(src)\n")


def test_sax_external_entities_disabled_is_clean():
    assert "xml_entity_expansion" not in detect_ext(
        "import xml.sax\nfrom xml.sax.handler import feature_external_ges\n"
        "def parse(src):\n"
        "    p = xml.sax.make_parser()\n"
        "    p.setFeature(feature_external_ges, False)\n"
        "    p.parse(src)\n")


def test_defusedxml_guard_disabled_flagged():
    assert "xml_entity_expansion" in detect_ext(
        "import defusedxml.ElementTree\n"
        "def parse(t):\n"
        "    return defusedxml.ElementTree.fromstring(t, forbid_entities=False)\n")
    assert "xml_entity_expansion" not in detect_ext(
        "import defusedxml.ElementTree\n"
        "def parse(t):\n"
        "    return defusedxml.ElementTree.fromstring(t)\n")


# --- CWE-400: resource exhaustion -----------------------------------------

def test_tainted_range_flagged():
    assert "resource_exhaustion" in detect_ext(
        "def alloc(request):\n"
        "    n = int(request.args.get('n'))\n"
        "    return [0 for _ in range(n)]\n")


def test_tainted_repetition_flagged():
    assert "resource_exhaustion" in detect_ext(
        "def alloc(request):\n"
        "    n = int(request.args.get('n'))\n"
        "    return 'x' * n\n")


def test_bounded_size_is_clean():
    assert "resource_exhaustion" not in detect_ext(
        "def alloc(request):\n"
        "    n = int(request.args.get('n'))\n"
        "    if n > 1024:\n"
        "        raise ValueError('too big')\n"
        "    return 'x' * n\n")


def test_tainted_decompress_flagged():
    assert "resource_exhaustion" in detect_ext(
        "import zlib\ndef inflate(request):\n"
        "    blob = request.get_data()\n"
        "    return zlib.decompress(blob)\n")


# --- pack invariants -------------------------------------------------------

def test_f2_zero_false_positives_on_original_fixtures():
    # promotion safety invariant: no F2 class may fire on ANY original
    # Stage-2C fixture (vulnerable ones carry other classes; secure ones
    # must stay completely clean)
    f2 = set(F2_CLASSES)
    for fx in fixtures():
        hit = detect_ext(fx["code"]) & f2
        assert not hit, f"{fx['id']} F2 false-positive: {hit}"


def test_f2_findings_carry_line_numbers():
    findings = detect_ext_findings(
        "import yaml\ndef f(t):\n    return yaml.unsafe_load(t)\n")
    assert any(cls == "unsafe_deserialization" and lineno == 3
               for cls, lineno, _ in findings)


def test_detect_classes_surfaces_f2():
    # public API (base | ext) must include the new classes
    assert "ssrf_taint" in detect_classes(
        "import requests\ndef fetch(request):\n"
        "    url = request.args.get('u')\n"
        "    return requests.get(url)\n")
