def test_notice_keeps_single_organization_scope(config):
    from pi_sandbox.integrations import Grants
    from pi_sandbox.platform import Platform
    from pi_sandbox.wrapper import notice

    text = notice(config, Platform('macos'), Grants(), [], config.paths.state, [])
    assert 'The token covers one organization' in text
    assert 'a private repository elsewhere answers 404 or asks for credentials by design' in text
