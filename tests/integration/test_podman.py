from __future__ import print_function, unicode_literals

from ..constants import FEDORA_MINIMAL_REPOSITORY, FEDORA_MINIMAL_REPOSITORY_TAG, \
    FEDORA_REPOSITORY

import subprocess
import time
import os

from conu.backend.podman.backend import PodmanBackend
from conu.backend.podman.container import PodmanRunBuilder, PodmanContainer
from conu.backend.podman.image import PodmanImagePullPolicy
from conu.utils import check_podman_command_works
from conu.utils.probes import Probe

from conu.apidefs.backend import CleanupPolicy
from conu.apidefs.metadata import ContainerStatus

from conu import ConuException, Directory

from six import string_types

import pytest

def test_podman_cli():
    """
    Test if podman CLI works
    """
    assert check_podman_command_works()


def test_podman_image():
    """
    Test interaction with an image
    """
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        assert "%s:%s" % (FEDORA_MINIMAL_REPOSITORY, FEDORA_MINIMAL_REPOSITORY_TAG) == image.get_full_name()
        assert "%s:%s" % (FEDORA_MINIMAL_REPOSITORY, FEDORA_MINIMAL_REPOSITORY_TAG) in image.inspect()['RepoTags']
        assert "ContainerConfig" in image.inspect()
        assert "fedora-minimal:26" in image.get_full_name()
        assert "registry.fedoraproject.org/fedora-minimal:26" == str(image)
        assert "PodmanImage(repository=%s, tag=%s)" % (FEDORA_MINIMAL_REPOSITORY,
                                                       FEDORA_MINIMAL_REPOSITORY_TAG) == repr(image)
        assert isinstance(image.get_id(), string_types)
        new_image = image.tag_image(tag="test")
        assert new_image.is_present()
        new_image.rmi(via_name=True)
        assert not new_image.is_present()


def test_image_wrong_types():
    with PodmanBackend() as backend:
        with pytest.raises(ConuException) as exc:
            backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, PodmanImagePullPolicy.NEVER)
            assert "tag" in exc.value.message


def test_container():
    """
    Basic tests of interacting with a podman container
    """
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        c = image.run_via_binary(
            PodmanRunBuilder(command=["cat"], additional_opts=["-i", "-t"])
        )
        try:
            assert c.is_running()
            assert "Config" in c.inspect()
            assert "Config" in c.inspect()
            assert c.get_id() == str(c)
            assert repr(c)
            assert isinstance(c.get_id(), string_types)
        finally:
            c.delete(force=True)


def test_container_create_failed():
    """
    Test podman run with execution non-existing command
    """
    ## FIXME: Cleanup containers after run
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        # should raise an exc, there is no such command: waldo; we need to find waldo first
        with pytest.raises(ConuException):
            image.run_via_binary(
                command=["waldo"]
            )
        c = image.run_via_binary_in_foreground(
            PodmanRunBuilder(command=["waldo"])
        )
        c.popen_instance.communicate()
        try:
            assert c.popen_instance.returncode > 0
        finally:
            c.delete(force=True)


def test_interactive_container():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        command = ["bash"]
        additional_opts = ["-i"]
        cont = image.run_via_binary_in_foreground(
            command=command, additional_opts=additional_opts,
            popen_params={"stdin": subprocess.PIPE, "stdout": subprocess.PIPE}
        )
        try:
            assert cont.is_running()
            assert "" == cont.logs()
            assert cont.is_running()
            time.sleep(0.1)
            cont.popen_instance.stdin.write(b"echo palacinky\n")
            cont.popen_instance.stdin.flush()
            time.sleep(0.2)
            assert b"palacinky" in cont.popen_instance.stdout.readline()
        finally:
            cont.delete(force=True)

def test_container_logs():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        command = ["bash", "-c", "for x in `seq 1 5`; do echo $x; done"]
        cont = image.run_via_binary(command=command)
        try:
            Probe(timeout=5, fnc=cont.get_status, expected_retval='exited').run()
            assert not cont.is_running()
            assert list(cont.logs()) == ['1', '\n', '2', '\n', '3', '\n', '4', '\n', '5', '\n']
        finally:
            cont.delete(force=True)

def test_http_client():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_REPOSITORY)
        c = image.run_via_binary(
            command=["python3", "-m", "http.server", "--bind", "0.0.0.0 8000"]
        )
        try:
            c.wait_for_port(8000)
            assert c.is_running()
            r = c.http_request(port="8000")
            assert "<!DOCTYPE HTML PUBLIC" in r.content.decode("utf-8")
            assert r.ok
            r2 = c.http_request(path="/etc", port="8000")
            assert "<!DOCTYPE HTML PUBLIC" in r2.content.decode("utf-8")
            assert "passwd" in r2.content.decode("utf-8")
            assert r2.ok
        finally:
            c.delete(force=True)

def test_http_client_context():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_REPOSITORY)
        c = image.run_via_binary(
            command=["python3", "-m", "http.server", "--bind", "0.0.0.0 8000"]
        )
        try:
            c.wait_for_port(8000)
            with c.http_client(port=8000) as session:
                r = session.get("/")
                assert r.ok
                assert "<!DOCTYPE HTML PUBLIC" in r.content.decode("utf-8")

                r2 = session.get("/etc")
                assert "<!DOCTYPE HTML PUBLIC" in r2.content.decode("utf-8")
                assert "passwd" in r2.content.decode("utf-8")
                assert r2.ok
        finally:
            c.delete(force=True)

def test_wait_for_status():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        cmd = ['sleep', '2']
        cont = image.run_via_binary(command=cmd)

        try:
            start = time.time()
            p = Probe(timeout=6, fnc=cont.get_status, expected_retval='exited')
            p.run()
            end = time.time() - start
            assert end > 2, "Probe should wait till container status is exited"
            assert end < 7, "Probe should end when container status is exited"
        finally:
            cont.delete(force=True)


def test_exit_code():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        cmd = ['sleep', '2']
        cont = image.run_via_binary(command=cmd)
        try:
            assert cont.is_running() and cont.exit_code() == 0
            p = Probe(timeout=5, fnc=cont.get_status, expected_retval='exited')
            p.run()
            assert not cont.is_running() and cont.exit_code() == 0
        finally:
            cont.delete(force=True)

        cmd = ['bash', '-c', "exit 42"]
        cont = image.run_via_binary(command=cmd)
        try:
            cont.wait()
            assert cont.exit_code() == 42
        finally:
            cont.delete(force=True)


def test_execute():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)
        cmd = ['sleep', 'infinity']
        cont = image.run_via_binary(command=cmd)
        cont.execute(["bash", "-c", "exit 0"])
        assert "asd\nasd" == cont.execute(["printf", "asd\nasd"])
        assert "asd" == cont.execute(["printf", "asd"])
        with pytest.raises(subprocess.CalledProcessError) as ex:
            cont.execute(["bash", "-c", "exit 110"])
            assert "exit code 110" in ex.value.message
            assert "bash" in ex.value.message


def test_pull_always():
    with PodmanBackend() as backend:
        image = backend.ImageClass("docker.io/library/busybox", tag="latest",
                                   pull_policy=PodmanImagePullPolicy.ALWAYS)
        try:
            assert image.is_present()
        finally:
            image.rmi(force=True)


def test_pull_if_not_present():
    with PodmanBackend() as backend:
        with pytest.raises(subprocess.CalledProcessError) as ex:
            backend.ImageClass._inspect("docker.io/library/busybox:latest")
            assert "not found" in ex.value.message
        image = backend.ImageClass("docker.io/library/busybox", tag="1.25.1")
        try:
            assert image.is_present()
        finally:
            image.rmi(force=True)


def test_pull_never():
    with PodmanBackend() as backend:
        with pytest.raises(subprocess.CalledProcessError):
            backend.ImageClass._inspect("busybox:1.25.1")
        image = backend.ImageClass("docker.io/library/busybox", tag="1.25.1",
                                   pull_policy=PodmanImagePullPolicy.NEVER)
        assert not image.is_present()


def test_set_name():
    with PodmanBackend() as backend:
        test_name = 'jondoe'
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG,
                                   pull_policy=PodmanImagePullPolicy.NEVER)
        cont = image.run_via_binary()
        assert cont.name
        cont.delete(force=True)

        cont = image.run_via_binary_in_foreground()
        assert cont.name
        cont.delete(force=True)

        additional_opts = ['--name', test_name]
        cont = image.run_via_binary(additional_opts=additional_opts)
        assert cont.name == test_name
        cont.delete(force=True)

        additional_opts = ['--name', test_name]
        cont = image.run_via_binary_in_foreground(additional_opts=additional_opts)
        assert cont.name == test_name
        cont.delete(force=True)


def test_run_with_volumes_metadata_check(tmpdir):
    with PodmanBackend() as backend:
        t = str(tmpdir)
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG,
                                   pull_policy=PodmanImagePullPolicy.NEVER)
        container = image.run_via_binary(volumes=(Directory(t), "/mountpoint", "Z"))
        try:
            mount = container.inspect()["Mounts"][0]
            assert mount["source"] == t
            assert mount["destination"] == "/mountpoint"
            assert "Z" in mount["options"]
        finally:
            container.delete(force=True)


def test_list_containers():
    with PodmanBackend() as backend:
        l = len(backend.list_containers())
        assert l >= 0
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG,
                                   pull_policy=PodmanImagePullPolicy.NEVER)
        prb = PodmanRunBuilder(command=["sleep", "1"], additional_opts=[
            "-e", "FOO=BAR",
            "-p", "1234"
        ])
        container = image.run_via_binary(run_command_instance=prb)
        try:
            container_list = backend.list_containers()
            l = len(container_list)
            assert l >= 1
            print(container_list[0].metadata.identifier)
            cont_under_test = [x for x in container_list
                               if x.metadata.identifier == container.get_id()][0]
            assert cont_under_test.metadata.image
            assert cont_under_test.metadata.command
            assert cont_under_test.metadata.env_variables["FOO"] == "BAR"
            assert cont_under_test.metadata.exposed_ports == [1234]
            assert cont_under_test.get_IPv4s()
        finally:
            container.delete(force=True)


def test_list_images():
    with PodmanBackend() as backend:
        image_list = backend.list_images()
        assert len(image_list) > 0
        the_id = "ded494ce3076e8f2d264235fdb09da5970921d8317f8fd024ab65821bf13e29f"
        image_under_test = [x for x in image_list if x.metadata.identifier == the_id][0]
        assert image_under_test.metadata.digest
        assert image_under_test.metadata.repo_digests


def test_layers():
    # TODO: Implement this test
    pass


def test_container_metadata():
    with PodmanBackend() as backend:
        image = backend.ImageClass(FEDORA_MINIMAL_REPOSITORY, tag=FEDORA_MINIMAL_REPOSITORY_TAG)

        c = image.run_via_binary(
            PodmanRunBuilder(command=["cat"], additional_opts=['-i',
                                                               '-t',
                                                               '--name', 'mycontainer',
                                                               '-p', '1234:12345',
                                                               '-p', '123:12345',
                                                               '-p', '8080',
                                                               '--hostname', 'my_hostname',
                                                               '-e', 'ENV1=my_env',
                                                               '-e', 'ASD=',
                                                               '-e', 'A=B=C=D',
                                                               '--label', 'testlabel1=testvalue1'
                                                               ])
        )

        try:
            container_metadata = c.get_metadata()

            assert container_metadata.command == ["cat"]
            assert container_metadata.name == "mycontainer"
            assert container_metadata.env_variables["ENV1"] == "my_env"
            assert container_metadata.env_variables["ASD"] == ""
            assert container_metadata.env_variables["A"] == "B=C=D"
            assert container_metadata.hostname == "my_hostname"

            # FIXME: podman raise an error when you send option  '-e XYZ': no such env variable
            # assert "XYZ" not in list(container_metadata.env_variables.keys())

            # FIXME: Podman store ports as integers not str
            assert 12345 in container_metadata.port_mappings
            assert container_metadata.port_mappings[12345] == [1234, 123]
            assert 8080 in container_metadata.port_mappings
            assert set(container_metadata.exposed_ports) == {8080, 12345}
            assert container_metadata.labels["testlabel1"] == "testvalue1"
            assert container_metadata.status == ContainerStatus.RUNNING
        finally:
            c.delete(force=True)
