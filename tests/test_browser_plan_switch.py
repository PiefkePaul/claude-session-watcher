from claude_session_watcher.browser import CamoufoxManager


def test_pro_org_id_from_organizations_prefers_claude_pro():
    organizations = [
        {"uuid": "free-org", "capabilities": ["chat"]},
        {"uuid": "pro-org", "capabilities": ["claude_pro", "chat"]},
    ]

    org_id = CamoufoxManager._pro_org_id_from_organizations(organizations)

    assert org_id == "pro-org"


def test_pro_org_id_from_organizations_returns_none_without_pro():
    organizations = [
        {"uuid": "free-org", "capabilities": ["chat"]},
        {"uuid": "api-org", "capabilities": ["api", "api_individual"]},
    ]

    org_id = CamoufoxManager._pro_org_id_from_organizations(organizations)

    assert org_id is None


def test_pro_org_id_from_organizations_ignores_invalid_payloads():
    organizations = [
        {"id": "", "capabilities": ["claude_pro"]},
        {"uuid": "x", "capabilities": "claude_pro"},
        None,
    ]

    org_id = CamoufoxManager._pro_org_id_from_organizations(organizations)

    assert org_id is None
