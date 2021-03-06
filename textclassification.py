#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import socketserver
import os
import sys
import socket
import hashlib
import struct
import json
import logging
from datetime import datetime
from setup_logging import setup_logging
from _version import __version__
from dataset import Dataset


class SimpleSocket(object):
    max_data_size = 4096

    def __init__(self, address=None, port=None):
        self.logger = logging.getLogger(__name__)
        self._timeout = None
        self._address = address
        self._port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.connect((self._address, self._port))
        except ConnectionError as err:
            self.logger.error("{} to {}:{}".format(err, self._address, self._port, err))
            sys.exit()

    def close(self):
        self.logger.debug("Closing main socket")
        self._close_connection()

    def _close_connection(self):
        self.socket.close()

    def send(self, data):
        size = len(data)
        packed_header = struct.pack('=I', size)
        self.socket.sendall(packed_header + data)

    def receive(self):
        packed_header = self.socket.recv(4)
        (size, ) = struct.unpack('=I', packed_header)
        if size == 0 or size > self.max_data_size:
            return None
        data = self.socket.recv(size)
        return data


class TextClassificationServer(object):
    """
    Class for using TextClassificationServer with a network socket
    """
    classifiers = dict()
    port = None

    def __init__(self, cfg=None):
        """
        class initialisation
        """
        self.logger = logging.getLogger(__name__)
        self.cfg = cfg
        self.server = None
        self.address = None
        self.port = None
        self.timeout = None
        for classifier_name in self.cfg['classifiers']:
            if classifier_name == "default":
                continue
            module_name = "classifier_" + classifier_name
            module = __import__(module_name)
            class_ = getattr(module, ''.join(module_name.title().split('_')))
            if class_ is not None:
                classifier = dict()
                classifier['enabled'] = self.cfg['classifiers'][classifier_name]['enabled']
                dataset_name = self.cfg['dataset']['name']
                classifier['class'] = class_(self.cfg['classifiers'][classifier_name],
                                             self.cfg['dataset']['categories'],
                                             dataset_name)
                TextClassificationServer.classifiers[classifier_name] = classifier

    def start(self, address=None, port=None, timeout=None, run_forever=True):
        """
        :param run_forever:
        :param address: hostname or ip address
        :param port: TCP port
        :param timeout: socket timeout
        :return:
        """
        if address:
            self.address = address
        else:
            self.address = self.cfg["address"]
        if port:
            self.port = port
        else:
            self.port = self.cfg["port"]
        if timeout:
            self.timeout = timeout
        else:
            self.timeout = self.cfg["timeout"]

        try:
            self.server = self.ThreadedTCPServer((self.address, self.port),
                                                 self.ThreadedTCPRequestHandler)
            self.server.socket.settimeout(self.timeout)
            self.server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Start a thread with the server -- that thread will then start one
            # more thread for each request
            server_thread = threading.Thread(target=self.server.serve_forever)
            # Exit the server thread when the main thread terminates
            server_thread.daemon = True
            server_thread.start()
            self.logger.info("Server loop running in thread: {}".format(server_thread.name))
            if run_forever:
                self.server.serve_forever()
        except socket.error:
            e = sys.exc_info()[1]
            raise ConnectionError(e)

    def shutdown(self):
        self.logger.info("Server shutdown")
        self.server.shutdown()

    class ThreadedTCPRequestHandler(socketserver.StreamRequestHandler):
        max_buffer_size = 4096

        def __init__(self, request, client_address, server):
            self.logger = logging.getLogger(__name__)
            super().__init__(request, client_address, server)

        def handle(self):

            try:
                cur_thread = threading.current_thread()
                while True:
                    data = self.receive()
                    if data is None:
                        break
                    data = data.rstrip()
                    self.logger.info("Thread {} received: {}".format(cur_thread.name, data))
                    header = data.split(b':')
                    if header[0] == b'PING':
                        self.ping()
                    elif header[0] == b'VERSION':
                        self.version()
                    elif header[0] == b'RELOAD':
                        self.reload()
                    elif header[0] == b'LIST_CLASSIFIER':
                        self.list_classifier()
                    elif header[0] == b'SET_CLASSIFIER':
                        classifier = header[1].decode('utf-8')
                        value = header[2].decode('utf-8')
                        self.set_classifier(classifier, value)
                    elif header[0] == b'MD5_FILE':
                        file_name = header[1]
                        self.md5_file(file_name=file_name)
                    elif header[0] == b'MD5_STREAM':
                        self.md5_stream()
                    elif header[0] == b'PREDICT_STREAM':
                        self.predict_stream()
                    elif header[0] == b'PREDICT_FILE':
                        file_name = header[1]
                        self.predict_file(file_name=file_name)
                    elif header[0] == b'CLOSE':
                        self.close()
                        break
                    else:
                        self.unknown_command()
            except socket.error:
                e = sys.exc_info()[1]
                raise ConnectionError(e)
            self.logger.info("Thread {} exit".format(cur_thread.name))

        def send(self, data):
            size = len(data)
            packed_header = struct.pack('=I', size)
            self.request.sendall(packed_header + data)

        def receive(self):
            packed_header = self.rfile.read(4)
            if packed_header == b'':
                return None
            (size, ) = struct.unpack('=I', packed_header)
            if size == 0 or size > self.max_buffer_size:
                return None
            data = self.rfile.read(size)
            return data

        def ping(self):
            response = dict()
            response["status"] = "OK"
            response["result"] = "PONG"
            response = json.dumps(response).encode('utf-8')
            self.send(response)

        def close(self):
            response = dict()
            response["status"] = "OK"
            response["result"] = "Bye"
            response = json.dumps(response).encode('utf-8')
            self.send(response)

        def version(self):
            response = dict()
            response["status"] = "OK"
            response["result"] = __version__
            response = json.dumps(response).encode('utf-8')
            self.send(response)

        def reload(self):
            response = b'reload'
            self.send(response)

        def list_classifier(self):
            response = dict()
            response["status"] = "OK"
            response["result"] = {classifier: TextClassificationServer.classifiers[classifier]['enabled']
                                  for classifier in TextClassificationServer.classifiers}
            response = json.dumps(response).encode('utf-8')
            self.send(response)

        def set_classifier(self, classifier, value):
            response = dict()
            response["status"] = "OK"
            if value.lower() == "true":
                TextClassificationServer.classifiers[classifier]['enabled'] = True
            elif value.lower() == "false":
                TextClassificationServer.classifiers[classifier]['enabled'] = False
            else:
                response["status"] = "ERROR"
            response["result"] = [{classifier: TextClassificationServer.classifiers[classifier]['enabled']}]
            response = json.dumps(response).encode('utf-8')
            self.send(response)

        def md5_file(self, file_name=None):
            # This function is just for testing purpose
            hash_md5 = hashlib.md5(open(file_name, 'rb').read())
            response = dict()
            if hash_md5:
                response["status"] = "OK"
                response["result"] = hash_md5.hexdigest()
                response = json.dumps(response).encode('utf-8')
            else:
                response["status"] = "Error"
                response["result"] = ""
                response = json.dumps(response).encode('utf-8')
            self.send(response)

        def md5_stream(self):
            # This function is just for testing purpose
            hash_md5 = hashlib.md5()
            while True:
                data = self.receive()
                if data is None:
                    break
                hash_md5.update(data)
            response = dict()
            if hash_md5:
                response["status"] = "OK"
                response["result"] = hash_md5.hexdigest()
                response = json.dumps(response).encode('utf-8')
            else:
                response["status"] = "Error"
                response["result"] = ""
                response = json.dumps(response).encode('utf-8')
            self.send(response)

        def predict_stream(self):
            stream = b''
            while True:
                data = self.receive()
                self.logger.debug("Data: {}".format(data))
                if data is None:
                    break
                stream += data
            stream = stream.decode('utf-8')
            multi_line = stream.split('\n')
            response = dict()
            response["status"] = "OK"
            result = dict()
            for classifier_name in TextClassificationServer.classifiers.keys():
                if TextClassificationServer.classifiers[classifier_name]['enabled']:
                    result[classifier_name] = \
                        TextClassificationServer.classifiers[classifier_name]['class'].predict(multi_line)
            response["result"] = result
            response = json.dumps(response).encode('utf-8')
            self.send(response)

        def predict_file(self, file_name=None):
            data = open(file_name, 'rb').read().decode('utf-8')
            multi_line = data.split('\n')
            response = dict()
            response["status"] = "OK"
            result = dict()
            for classifier_name in TextClassificationServer.classifiers.keys():
                if TextClassificationServer.classifiers[classifier_name]['enabled']:
                    result[classifier_name] = \
                        TextClassificationServer.classifiers[classifier_name]['class'].predict(multi_line)
            response["result"] = result
            response = json.dumps(response).encode('utf-8')
            self.send(response)

        def unknown_command(self):
            response = dict()
            response["status"] = "ERROR"
            response["result"] = "Unknown Command"
            response = json.dumps(response).encode('utf-8')
            self.send(response)

    class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        allow_reuse_address = True
        pass


class TextClassificationClient(object):
    max_chunk_size = 1024

    def __init__(self, address='localhost', port=3333):
        setup_logging()
        self.address = address
        self.port = port
        self.simple_socket = SimpleSocket(address=self.address, port=self.port)

    def command(self, message=None):
        logger = logging.getLogger(__name__)
        try:
            self.simple_socket.send(message.encode('utf-8'))
            response = self.simple_socket.receive()
            logger.debug("command Received: {}".format(response))
            return response
        except ConnectionError as err:
            logger.error("OS error: {0}".format(err))

    def md5_file(self, file_name=None):
        # This function is just for testing purpose
        logger = logging.getLogger(__name__)
        try:
            statinfo = os.stat(file_name)
            if statinfo is not None:
                command = "MD5_FILE:{}\n".format(file_name)
                self.simple_socket.send(command.encode('utf-8'))
                response = self.simple_socket.receive()
                logger.debug("Client Received: {}".format(response))
                return response
            else:
                return None
        except OSError as err:
            logger.error("OS error: {0}".format(err))

    def md5_stream(self, data=None):
        # This function is just for testing purpose
        logger = logging.getLogger(__name__)
        command = "MD5_STREAM\n"
        self.simple_socket.send(command.encode('utf-8'))
        try:
            data_len = len(data)
            start_pos = 0
            end_pos = self.max_chunk_size
            while start_pos < data_len:
                end_pos = min(end_pos, data_len)
                self.simple_socket.send(data[start_pos:end_pos])
                start_pos += self.max_chunk_size
                end_pos += self.max_chunk_size
            self.simple_socket.send(b'')
            response = self.simple_socket.receive()
            logger.debug("Client Received: {}".format(response))
            return response
        except OSError as err:
            logger.error("OS error: {0}".format(err))

    def predict_stream(self, data=None):
        logger = logging.getLogger(__name__)
        command = "PREDICT_STREAM\n"
        self.simple_socket.send(command.encode('utf-8'))
        try:
            data_len = len(data)
            start_pos = 0
            end_pos = self.max_chunk_size
            while start_pos < data_len:
                end_pos = min(end_pos, data_len)
                self.simple_socket.send(data[start_pos:end_pos])
                start_pos += self.max_chunk_size
                end_pos += self.max_chunk_size
            self.simple_socket.send(b'')
            response = self.simple_socket.receive()
            logger.debug("Client Received: {}".format(response))
            return response
        except OSError as err:
            logger.error("OS error: {0}".format(err))

    def predict_file(self, file_name=None):
        logger = logging.getLogger(__name__)
        try:
            statinfo = os.stat(file_name)
            if statinfo is not None:
                command = "PREDICT_FILE:{}\n".format(file_name)
                self.simple_socket.send(command.encode('utf-8'))
                response = self.simple_socket.receive()
                logger.debug("Client Received: {}".format(response))
                return response
            else:
                return None
        except OSError as err:
            logger.error("OS error: {0}".format(err))

    def set_classifier(self, classifier=None, value=None):
        logger = logging.getLogger(__name__)
        command = "SET_CLASSIFIER:{}:{}\n".format(classifier, value)
        self.simple_socket.send(command.encode('utf-8'))
        response = self.simple_socket.receive()
        logger.debug("Client Received: {}".format(response))
        return response


class TextClassificationTraining(object):
    """
    Class for using TextClassificationServer with a network socket
    """
    classifiers = dict()

    def __init__(self, cfg):
        """
        class initialisation
        """
        self.logger = logging.getLogger(__name__)
        self.cfg = cfg

        try:
            os.makedirs(self.cfg["result_dir"], exist_ok=True)
        except OSError as err:
            self.logger.error("OS error: {0}".format(err))

        for classifier_name in self.cfg["classifiers"]:
            if classifier_name == "default":
                continue
            module_name = "classifier_" + classifier_name
            module = __import__(module_name)
            class_ = getattr(module, ''.join(module_name.title().split('_')))
            if class_:
                classifier = dict()
                classifier["enabled"] = self.cfg["classifiers"][classifier_name]["enabled"]
                default_dataset = self.cfg["datasets"]["default"]
                classifier["class"] = class_(self.cfg["classifiers"][classifier_name],
                                             self.cfg["datasets"][default_dataset]["categories"],
                                             default_dataset, False)
                TextClassificationTraining.classifiers[classifier_name] = classifier

    def start(self, cn=None, dn=None):
        self.logger.info("Training starts")
        if cn:
            classifier_name = cn
        else:
            classifier_name = self.cfg["classifiers"]["default"]
        if classifier_name not in TextClassificationTraining.classifiers.keys() and classifier_name != "all":
            print("The classifier {} doesn't exist".format(classifier_name))
            return 1
        if dn:
            dataset_name = dn
        else:
            dataset_name = self.cfg["datasets"]["default"]
        if dataset_name not in self.cfg["datasets"]:
            print("The dataset {} doesn't exist".format(dataset_name))
            return 1
        dataset = Dataset.create_dataset(self.cfg["datasets"][dataset_name])
        now = datetime.now().strftime('%Y%m%d_%H%M%S')
        TCT = TextClassificationTraining
        if classifier_name == "all":
            for classifier_name in TCT.classifiers:
                result_name = "{}/{}_{}_{}".format(self.cfg["result_dir"], classifier_name, dataset_name, now)
                TCT.classifiers[classifier_name]["class"].fit(dataset, result_name)
                print("The training of {} classifier for the dataset {} is done.".format(classifier_name, dataset_name))
                print("The result is saved in: {}(.pkl)".format(result_name))
        else:
            result_name = "{}/{}_{}_{}".format(self.cfg["result_dir"], classifier_name, dataset_name, now)
            TCT.classifiers[classifier_name]["class"].fit(dataset, result_name)
            print("The training of {} classifier for the dataset {} is done.".format(classifier_name, dataset_name))
            print("The result is saved in: {}(.pkl)".format(result_name))

        self.logger.info("Training end")
