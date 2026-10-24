import socket

def deny(*args, **kwargs):
    raise OSError('network disabled for local audit tests')

socket.socket.connect = deny
socket.create_connection = deny
