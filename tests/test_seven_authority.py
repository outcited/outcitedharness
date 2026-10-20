"""Local regression evidence; no Seven host or model requests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from director_consumer.llm import Endpoint, OpsBrainClient
from director_consumer.policy import ModelPolicy
from director_consumer.state import DirectorState
from director_consumer.writer_lock import workspace_writer

ROOT = Path(__file__).resolve().parents[1]


def test_alternating_connections_preserve_journal(tmp_path):
    a, b = DirectorState(tmp_path), DirectorState(tmp_path)
    try:
        a.journal_run({'writer': 'a'})
        b.journal_run({'writer': 'b'})
        a.journal_run({'writer': 'a again'})
        assert a.verify_chain() == (True, None)
    finally:
        a.close()
        b.close()


def test_concurrent_journal_writers_preserve_every_receipt(tmp_path):
    DirectorState(tmp_path).close()
    def write(writer):
        st = DirectorState(tmp_path)
        try:
            for n in range(30):
                st.journal_run({'writer': writer, 'n': n})
        finally:
            st.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, ('a', 'b')))
    st = DirectorState(tmp_path)
    try:
        assert st.verify_chain() == (True, None)
        assert st.con.execute('SELECT count(*) FROM journal').fetchone()[0] == 60
    finally:
        st.close()


def test_run_lock_contends_across_processes_and_releases(tmp_path):
    import subprocess
    import sys
    command = [sys.executable, '-c',
               'from pathlib import Path; from director_consumer.writer_lock '
               'import workspace_writer; '
               f'\nwith workspace_writer(Path({str(tmp_path)!r})): pass']
    with workspace_writer(tmp_path):
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode != 0
        assert 'authority already held' in result.stderr
    assert subprocess.run(command, capture_output=True).returncode == 0


def test_run_lock_released_on_exception(tmp_path):
    with pytest.raises(ValueError), workspace_writer(tmp_path):
        raise ValueError('crash')
    with workspace_writer(tmp_path):
        pass


def test_asus_only_default_and_project_outage_has_no_fallback():
    for client in (OpsBrainClient(), OpsBrainClient(policy=ModelPolicy.load(
            ROOT / 'projects/designwins/policy.yaml'))):
        with patch.object(client, '_call', side_effect=OSError('offline')) as call:
            assert 'unavailable' in client.assess('event', 'P0')
            assert call.call_count == 1
            ep = call.call_args.args[0]
            assert ep.base_url == 'http://100.68.133.1:8900'
            assert ep.model == 'qwen3.8-27b-nvfp4'


def test_late_policy_attachment_replaces_same_name_url_and_model():
    client = OpsBrainClient(endpoints=[Endpoint('asus2_ops_brain',
                                              'http://wrong-host', 'wrong-model')])
    client.policy = ModelPolicy.load(ROOT / 'projects/designwins/policy.yaml')
    with patch.object(client, '_call', return_value='advisory') as call:
        result = client.assess('event', 'P0')
        assert result['verified'] is False
        assert call.call_args.args[0].base_url == 'http://100.68.133.1:8900'
        assert call.call_args.args[0].model == 'qwen3.8-27b-nvfp4'
