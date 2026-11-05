import copy
import pytest
from spot_os.hardware_acceptance import compare,doctor_command


@pytest.mark.parametrize('version',['0.1.4','0.1.6','0.1.7'])
@pytest.mark.parametrize('bridge',[False,True])
def test_doctor_selection_has_no_assertion_traceback(version,bridge):
    cmd=doctor_command(version,bridge)
    assert ('bridge_entry.py' in ' '.join(cmd))==(version=='0.1.4' and bridge)


def fixture():
    before=dict(boot='b',kernel='k',app={'version':'0.28.13'},hashes={'binary':'h'},
        cci_lock={'inode':12},streamer='pid=123 invocation=x restarts=0',os='0.1.6')
    after=dict(before,os='0.1.7',doctor={'ok':True},transaction={'phase':'success','handover_confirmed':True},floor={'sequence':107})
    return before,after


def test_same_boot_upgrade_evidence_is_read_only_and_complete():
    before,after=fixture();saved=copy.deepcopy((before,after))
    assert compare(before,after)['ok']
    assert saved==(before,after)


@pytest.mark.parametrize('name',['boot','kernel','app','hashes','cci_lock','streamer'])
def test_each_protected_category_must_match(name):
    before,after=fixture();after[name]='changed'
    with pytest.raises(ValueError,match='Bestandsschutz'):compare(before,after)


@pytest.mark.parametrize('name,value',[('os','0.1.6'),('doctor',{'ok':False}),
    ('transaction',{'phase':'success','handover_confirmed':False}),('floor',{'sequence':106})])
def test_unconfirmed_completion_is_not_acceptance(name,value):
    before,after=fixture();after[name]=value
    with pytest.raises(ValueError):compare(before,after)
