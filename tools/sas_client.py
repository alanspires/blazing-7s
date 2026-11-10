"""One long poll by command, with its reply decoded: what the CLI and the write test send."""

from __future__ import annotations

import sas_commands as sc
import sas_protocol as sas


def failure(error: BaseException) -> dict:
    """A result's error fields: the error's message and its type's name."""
    return {"error": str(error), "error_type": type(error).__name__}


def poll(host: sas.Host, command: sc.Command, values: list[str], *, retry: bool = True) -> dict:
    """The result holds the reply's data and its fields ("ACK" for a bare ACK), or an error
    and its type (NoResponse, Nacked, ...)."""
    result = {"command": command.name, "code": f"{command.code:02X}", "values": values}
    data = sc.encode_request(command, values)
    try:
        reply = host.long_poll(command.code, data, command.crc, retry=retry)
    except sas.ProtocolError as e:
        return result | failure(e)
    result["data"] = None if reply is None else reply.hex(" ")
    try:
        result["reply"] = "ACK" if reply is None else dict(sc.decode_reply(command, reply))
    except sc.DecodeError as e:
        result |= failure(e)
    return result
