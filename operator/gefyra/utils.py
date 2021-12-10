import logging
import select
import tarfile
from collections import defaultdict
from tempfile import TemporaryFile

import kubernetes as k8s
from websocket import ABNF

logger = logging.getLogger("gefyra.utils")


class WSFileManager:
    """
    WS wrapper to manage read and write bytes in K8s WSClient
    """

    def __init__(self, ws_client):
        """

        :param wsclient: Kubernetes WSClient
        """
        self.ws_client = ws_client

    def read_bytes(self, timeout=0):
        """
        Read slice of bytes from stream

        :param timeout: read timeout
        :return: stdout, stderr and closed stream flag
        """
        stdout_bytes = None
        stderr_bytes = None

        if self.ws_client.is_open():
            if not self.ws_client.sock.connected:
                self.ws_client._connected = False
            else:
                r, _, _ = select.select((self.ws_client.sock.sock,), (), (), timeout)
                if r:
                    op_code, frame = self.ws_client.sock.recv_data_frame(True)
                    if op_code == ABNF.OPCODE_CLOSE:
                        self.ws_client._connected = False
                    elif op_code == ABNF.OPCODE_BINARY or op_code == ABNF.OPCODE_TEXT:
                        data = frame.data
                        if len(data) > 1:
                            channel = data[0]
                            data = data[1:]
                            if data:
                                if channel == k8s.stream.ws_client.STDOUT_CHANNEL:
                                    stdout_bytes = data
                                elif channel == k8s.stream.ws_client.STDERR_CHANNEL:
                                    stderr_bytes = data
        return stdout_bytes, stderr_bytes, not self.ws_client._connected


def stream_copy_from_pod(pod_name, namespace, source_path, destination_path):
    # https://stackoverflow.com/questions/59703610/copy-file-from-pod-to-host-by-using-kubernetes-python-client

    """
    Copy file from pod to the host.

    :param pod_name: String. Pod name
    :param namespace: String. Namespace
    :param source_path: String. Pod destination file path
    :param destination_path: Host destination file path
    :return: bool
    """

    core_v1_api = k8s.client.CoreV1Api()

    command_copy = ["tar", "cf", "-", source_path]
    with TemporaryFile() as tar_buffer:
        exec_stream = k8s.stream.stream(
            core_v1_api.connect_get_namespaced_pod_exec,
            pod_name,
            namespace,
            command=command_copy,
            stderr=True,
            stdin=True,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        # Copy file to stream
        try:
            reader = WSFileManager(exec_stream)
            while True:
                out, err, closed = reader.read_bytes()
                if out:
                    tar_buffer.write(out)
                elif err:
                    logger.debug(
                        "Error copying file {0}".format(err.decode("utf-8", "replace"))
                    )
                if closed:
                    break
            exec_stream.close()
            tar_buffer.flush()
            tar_buffer.seek(0)
            with tarfile.open(fileobj=tar_buffer, mode="r:") as tar:
                member = tar.getmember(source_path.split("/", 1)[1])
                tar.makefile(member, destination_path)
                return True
        except Exception as e:
            logger.info(e)
            raise e


def read_wireguard_config(raw: str) -> dict:
    data = defaultdict(dict)
    _prefix = "none"
    for line in raw.split("\n"):
        try:
            if line.strip() == "":
                continue
            elif "[Interface]" in line:
                _prefix = "Interface"
                continue
            elif "[Peer]" in line:
                _prefix = "Peer"
                continue
            key, value = line.split("=", 1)
            data[f"{_prefix}.{key.strip()}"] = value.strip()
        except Exception as e:
            logger.exception(e)
    return data
