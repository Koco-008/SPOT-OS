from spot_os.cli import boot_guard
from spot_os.common import atomic


def test_count_once_per_boot_and_limit_without_disabling_recovery_display(tmp_path):
    state,boot=tmp_path/'state.json',tmp_path/'boot'
    for number in range(1,5):
        boot.write_text(str(number))
        result=boot_guard(state,boot)
        assert result['attempts']==number
        assert result['blocked'] is (number>3)
        assert boot_guard(state,boot)['attempts']==number
    atomic(state,dict(attempts=0))
    assert not boot_guard(state,boot)['blocked']
