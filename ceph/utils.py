import datetime
import traceback

import re
import yaml
import random
import logging
import time
import os
import requests
import re
import datetime

from gevent import sleep
from mita.openstack import CephVMNode
from libcloud.compute.types import Provider
from libcloud.compute.providers import get_driver
from parallel import parallel

log = logging.getLogger(__name__)


def create_ceph_nodes(gyaml, osp_cred):
    var = yaml.safe_load(open(gyaml))
    glbs = var.get('globals')
    ovar = yaml.safe_load(open(osp_cred))
    osp_glbs = ovar.get('globals')
    os_cred = osp_glbs.get('openstack-credentials')
    params = dict()
    params['cloud-data'] = glbs.get('cloud-data')
    params['username'] = os_cred['username']
    params['password'] = os_cred['password']
    params['auth-url'] = os_cred['auth-url']
    params['auth-version'] = os_cred['auth-version']
    params['tenant-name'] = os_cred['tenant-name']
    params['service-region'] = os_cred['service-region']
    params['keypair'] = os_cred.get('keypair', None)
    ceph_cluster = glbs.get('ceph-cluster')
    ceph_nodes = dict()
    if ceph_cluster.get('create'):
        params['image-name'] = ceph_cluster.get('image-name')
        params['cluster-name'] = ceph_cluster.get('name')
        params['vm-size'] = ceph_cluster.get('vm-size')
        if params.get('root-login') is False:
            params['root-login'] = False
        else:
            params['root-login'] = True
            run_name = "run" + str(random.randint(10, 999)) + "-"
        with parallel() as p:
            for node in range(1, 100):
                node = "node" + str(node)
                if not ceph_cluster.get(node):
                    break
                node_dict = ceph_cluster.get(node)
                params['role'] = node_dict.get('role')
                role = params['role']
                if params.get('run'):
                    log.info("Using existing run name")
                else:
                    user = os.getlogin()
                    params['run'] = run_name
                params['node-name'] = 'ceph-' + user + \
                    '-' + params['run'] + node + '-' + role
                if role == 'osd':
                    params['no-of-volumes'] = node_dict.get('no-of-volumes')
                    params['size-of-disks'] = node_dict.get('disk-size')
                if node_dict.get('image-name'):
                    params['image-name'] = node_dict.get('image-name')
                if node_dict.get('cloud-data'):
                    params['cloud-data'] = node_dict.get('cloud-data')
                #ceph_nodes[node] = CephVMNode(**params)
                del params['run']
                p.spawn(setup_vm_node, node, ceph_nodes, **params)
    log.info("Done creating nodes")
    return ceph_nodes


def setup_vm_node(node, ceph_nodes, **params):
    ceph_nodes[node] = CephVMNode(**params)


def get_openstack_driver(yaml):
    OpenStack = get_driver(Provider.OPENSTACK)
    glbs = yaml.get('globals')
    os_cred = glbs.get('openstack-credentials')
    username = os_cred['username']
    password = os_cred['password']
    auth_url = os_cred['auth-url']
    auth_version = os_cred['auth-version']
    tenant_name = os_cred['tenant-name']
    service_region = os_cred['service-region']
    driver = OpenStack(
        username,
        password,
        ex_force_auth_url=auth_url,
        ex_force_auth_version=auth_version,
        ex_tenant_name=tenant_name,
        ex_force_service_region=service_region
    )
    return driver


def cleanup_ceph_nodes(gyaml, name=None, timeout=300):
    user = os.getlogin()
    if name is None:
        name = 'ceph-' + user
    var = yaml.safe_load(open(gyaml))
    driver = get_openstack_driver(var)
    timeout = datetime.timedelta(seconds=timeout)
    with parallel() as p:
        for node in driver.list_nodes():
            if node.name.startswith(name):
                for ip in node.public_ips:
                    log.info("removing ip %s from node %s", ip, node.name)
                    driver.ex_detach_floating_ip_from_node(node, ip)
                starttime = datetime.datetime.now()
                log.info(
                    "Destroying node {node_name} with {timeout} timeout".format(node_name=node.name, timeout=timeout))
                while True:
                    try:
                        p.spawn(node.destroy)
                        break
                    except AttributeError:
                        if datetime.datetime.now() - starttime > timeout:
                            raise RuntimeError(
                                "Failed to destroy node {node_name} with {timeout} timeout:\n{stack_trace}".format(
                                    node_name=node.name,
                                    timeout=timeout, stack_trace=traceback.format_exc()))
                        else:
                            sleep(1)
                sleep(5)
    with parallel() as p:
        for fips in driver.ex_list_floating_ips():
            if fips.node_id is None:
                log.info("Releasing ip %s", fips.ip_address)
                driver.ex_delete_floating_ip(fips)
    with parallel() as p:
        for volume in driver.list_volumes():
            if volume.name is None:
                log.info("Volume has no name, skipping")
            elif volume.name.startswith(name):
                log.info("Removing volume %s", volume.name)
                sleep(10)
                volume.destroy()


def keep_alive(ceph_nodes):
    for node in ceph_nodes:
        node.exec_command(cmd='uptime', check_ec=False)


def setup_repos(ceph, base_url, installer_url=None):
    repos = ['MON', 'OSD', 'Tools', 'Calamari', 'Installer']
    base_repo = generate_repo_file(base_url, repos)
    base_file = ceph.write_file(
        sudo=True,
        file_name='/etc/yum.repos.d/rh_ceph.repo',
        file_mode='w')
    base_file.write(base_repo)
    base_file.flush()
    if installer_url is not None:
        installer_repos = ['Agent', 'Main', 'Installer']
        inst_repo = generate_repo_file(installer_url, installer_repos)
        log.info("Setting up repo on %s", ceph.hostname)
        inst_file = ceph.write_file(
            sudo=True,
            file_name='/etc/yum.repos.d/rh_ceph_inst.repo',
            file_mode='w')
        inst_file.write(inst_repo)
        inst_file.flush()


def check_ceph_healthly(ceph_mon, num_osds, num_mons, timeout=300):
    """
    Function to check ceph is in healthy state

    Args:
       ceph_mon: monitor node
       num_osds: number of osds in cluster
       num_mons: number of mons in cluster
       timeout: 300 seconds(default) max time to check
         if cluster is not healthy within timeout period
                return 1

    Returns:
       return 0 when ceph is in healthy state, else 1
    """

    timeout = datetime.timedelta(seconds=timeout)
    starttime = datetime.datetime.now()
    while datetime.datetime.now() - starttime <= timeout:
        out, err = ceph_mon.exec_command(cmd='sudo ceph -s')
        lines = out.read()
        if 'peering' not in lines and 'activating' not in lines and \
           'creating' not in lines:
            break
        sleep(1)
    m = re.search(r"(\d+)\s+osds:\s+(\d+)\s+up,\s+(\d+)\s+in", lines)
    all_osds = int(m.group(1))
    up_osds = int(m.group(2))
    in_osds = int(m.group(3))
    if num_osds != all_osds:
        log.info("Not all osd's are up")
        return 1
    if up_osds != in_osds:
        log.info("Not all osd's are in")
        return 1
    m = re.search(r"(\d+) daemons, quorum", lines)
    all_mons = int(m.group(1))
    if all_mons != num_mons:
        log.info("Not all monitors are in cluster")
        return 1
    if "HEALTH_ERR" in lines:
        log.info("HEALTH in ERROR STATE")
        return 1
    return 0


def generate_repo_file(base_url, repos):
    repo_file = ''
    for repo in repos:
        repo_to_use = base_url + "compose/" + repo + "/x86_64/os/"
        r = requests.get(repo_to_use, timeout=10)
        log.info("Checking %s", repo_to_use)
        if r.status_code == 200:
            log.info("Using %s", repo_to_use)
            header = "[ceph-" + repo + "]" + "\n"
            name = "name=ceph-" + repo + "\n"
            baseurl = "baseurl=" + repo_to_use + "\n"
            gpgcheck = "gpgcheck=0\n"
            enabled = "enabled=1\n\n"
            repo_file = repo_file + header + name + baseurl + \
                gpgcheck + enabled
    return repo_file

def get_iso_file_url(base_url):
    iso_file_path = base_url + "compose/Tools/x86_64/iso/"
    iso_dir_html = requests.get(iso_file_path, timeout=10).content
    match = re.search('<a href="(.*?)">(.*?)-x86_64-dvd.iso</a>', iso_dir_html)
    iso_file_name = match.group(1)
    log.info('Using {}'.format(iso_file_name))
    iso_file = iso_file_path + iso_file_name
    return iso_file

def create_ceph_conf(fsid, mon_hosts, pg_num='128', pgp_num='128', size='2',
                     auth='cephx', pnetwork='172.16.0.0/12',
                     jsize='1024'):
    fsid = 'fsid = ' + fsid + '\n'
    mon_init_memb = 'mon initial members = '
    mon_host = 'mon host = '
    public_network = 'public network = ' + pnetwork + '\n'
    auth = 'auth cluster required = cephx\nauth service \
            required = cephx\nauth client required = cephx\n'
    jsize = 'osd journal size = ' + jsize + '\n'
    size = 'osd pool default size = ' + size + '\n'
    pgnum = 'osd pool default pg num = ' + pg_num + '\n'
    pgpnum = 'osd pool default pgp num = ' + pgp_num + '\n'
    for mhost in mon_hosts:
        mon_init_memb = mon_init_memb + mhost.shortname + ','
        mon_host = mon_host + mhost.internal_ip + ','
    mon_init_memb = mon_init_memb[:-1] + '\n'
    mon_host = mon_host[:-1] + '\n'
    conf = '[global]\n'
    conf = conf + fsid + mon_init_memb + mon_host + public_network + auth + \
        size + jsize + pgnum + pgpnum
    return conf


def setup_deb_repos(node, ubuntu_repo):
    node.exec_command(cmd='sudo rm -f /etc/apt/sources.list.d/*')
    repos = ['MON', 'OSD', 'Tools']
    for repo in repos:
        cmd = 'sudo echo deb ' + ubuntu_repo + '/{0}'.format(repo) + \
              ' $(lsb_release -sc) main'
        node.exec_command(cmd=cmd + ' > ' + "/tmp/{0}.list".format(repo))
        node.exec_command(cmd='sudo cp /tmp/{0}.list'.format(repo) +
                          ' /etc/apt/sources.list.d/')
    ds_keys = ['https://www.redhat.com/security/897da07a.txt',
               'https://www.redhat.com/security/f21541eb.txt',
               'http://puddle.ceph.redhat.com/keys/RPM-GPG-KEY-redhatbuild']

    for key in ds_keys:
        wget_cmd = 'sudo wget -O - ' + key + ' | sudo apt-key add -'
        node.exec_command(cmd=wget_cmd)
    node.exec_command(cmd='sudo apt-get update')


def setup_cdn_repos(ceph_nodes, build=None):
    repos_13x = ['rhel-7-server-rhceph-1.3-mon-rpms',
                 'rhel-7-server-rhceph-1.3-osd-rpms',
                 'rhel-7-server-rhceph-1.3-calamari-rpms',
                 'rhel-7-server-rhceph-1.3-installer-rpms',
                 'rhel-7-server-rhceph-1.3-tools-rpms']

    repos_20 = ['rhel-7-server-rhceph-2-mon-rpms',
                'rhel-7-server-rhceph-2-osd-rpms',
                'rhel-7-server-rhceph-2-tools-rpms',
                'rhel-7-server-rhscon-2-agent-rpms',
                'rhel-7-server-rhscon-2-installer-rpms',
                'rhel-7-server-rhscon-2-main-rpms']

    repos_30 = ['rhel-7-server-rhceph-3-mon-rpms',
                'rhel-7-server-rhceph-3-osd-rpms',
                'rhel-7-server-rhceph-3-tools-rpms',
                'rhel-7-server-extras-rpms']

    if build.startswith('1'):
        repos = repos_13x
    elif build.startswith('2'):
        repos = repos_20
    elif build.startswith('3'):
        repos = repos_30
    with parallel() as p:
        for node in ceph_nodes:
            p.spawn(set_cdn_repo, node, repos)


def set_cdn_repo(node, repos):
        for repo in repos:
            node.exec_command(
                sudo=True, cmd='subscription-manager repos --enable={r}'.format(r=repo))
        node.exec_command(sudo=True, cmd='subscription-manager refresh')

def update_ca_cert(node, cert_url, timeout=120):
    if node.pkg_type == 'deb':
        cmd = 'cd /usr/local/share/ca-certificates/ && {{ sudo curl -O {url} ; cd -; }}'.format(url=cert_url)
        node.exec_command(cmd=cmd, timeout=timeout)
        node.exec_command(cmd='sudo update-ca-certificates', timeout=timeout)
    else:
        cmd = 'cd /etc/pki/ca-trust/source/anchors && {{ sudo curl -O {url} ; cd -; }}'.format(url=cert_url)
        node.exec_command(cmd=cmd, timeout=timeout)
        node.exec_command(cmd='sudo update-ca-trust extract', timeout=timeout)