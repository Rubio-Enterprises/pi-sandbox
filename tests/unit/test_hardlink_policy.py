import os

import pytest

from pi_sandbox import Refusal, hooks
from pi_sandbox.config import load_layer
from tests.support import executable


@pytest.mark.parametrize('name', ['layer.toml', 'profile.json'])
def test_hardlinked_policy_is_refused_before_parsing(fixture, name):
    directory = fixture.layer('95-hardlink', 'schema = 1\n', {})
    source = directory / name
    source.write_bytes(b'not valid TOML or JSON')
    alias = fixture.repo / name
    alias.hardlink_to(source)
    assert source.stat().st_nlink == 2
    with pytest.raises(Refusal, match='exactly one hard link'):
        load_layer(directory, fixture.paths)


def test_hardlinked_user_hook_is_refused(fixture):
    source = executable(fixture.paths.home / '.local/libexec/hook', '#!/bin/sh\nexit 0\n')
    (fixture.repo / 'hook-alias').hardlink_to(source)
    assert source.stat().st_nlink == 2
    with pytest.raises(Refusal, match='exactly one hard link'):
        hooks.executable([str(source)], fixture.repo, [str(fixture.repo)])


@pytest.mark.parametrize('writable', [False, True])
def test_root_owned_multilink_hook_exemption_requires_no_user_write(fixture, monkeypatch, writable):
    source = executable(fixture.bins / 'system-standin', '#!/bin/sh\nexit 0\n')
    real = os.fstat
    checked = []

    def root_owned(fd):
        info = list(real(fd))
        info[3], info[4] = 2, 0
        return os.stat_result(info)

    def access(path, mode, *, effective_ids):
        checked.append((path, mode, effective_ids))
        return writable

    monkeypatch.setattr(hooks.os, 'fstat', root_owned)
    monkeypatch.setattr(hooks.os, 'access', access)
    if not writable and os.geteuid() != 0:
        assert hooks.executable([str(source)], fixture.repo, []) == [str(source)]
        assert checked == [(source.resolve(), os.W_OK, True)]
    else:
        with pytest.raises(Refusal, match='exactly one hard link'):
            hooks.executable([str(source)], fixture.repo, [])


def test_nonroot_foreign_multilink_hook_has_no_system_exemption(fixture, monkeypatch):
    source = executable(fixture.bins / 'foreign-standin', '#!/bin/sh\nexit 0\n')
    real = os.fstat

    def foreign(fd):
        info = list(real(fd))
        info[3], info[4] = 2, os.geteuid() + 1
        return os.stat_result(info)

    monkeypatch.setattr(hooks.os, 'fstat', foreign)
    with pytest.raises(Refusal, match='exactly one hard link'):
        hooks.executable([str(source)], fixture.repo, [])
