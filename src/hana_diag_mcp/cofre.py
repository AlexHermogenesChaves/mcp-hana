"""Criptografia do cadastro de conexões com o DPAPI do Windows.

A chave é derivada pelo próprio Windows a partir do login do usuário: não existe
arquivo de chave nem senha mestra. Só o mesmo usuário do Windows, na mesma
máquina (ou com perfil móvel), consegue decifrar. Outro usuário, ou o arquivo
copiado para outro computador, não abre.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

CABECALHO = b"HDIAG-DPAPI-1\n"
_ENTROPIA = b"hana-diag-mcp/conexoes/v1"  # segredo adicional fixo da aplicação
_SEM_INTERFACE = 0x1  # CRYPTPROTECT_UI_FORBIDDEN


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _api():
    if sys.platform != "win32":
        raise RuntimeError("A criptografia do cadastro usa o DPAPI e só funciona no Windows.")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    assinatura = [
        ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    crypt32.CryptProtectData.argtypes = assinatura
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def _blob(dados: bytes) -> tuple[_Blob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(dados, len(dados))
    return _Blob(len(dados), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _ler_saida(kernel32, saida: _Blob) -> bytes:
    try:
        return ctypes.string_at(saida.pbData, saida.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(saida.pbData, ctypes.c_void_p))


def cifrar(dados: bytes) -> bytes:
    crypt32, kernel32 = _api()
    entrada, _b1 = _blob(dados)
    entropia, _b2 = _blob(_ENTROPIA)
    saida = _Blob()
    if not crypt32.CryptProtectData(
        ctypes.byref(entrada), "hana-diag conexoes", ctypes.byref(entropia),
        None, None, _SEM_INTERFACE, ctypes.byref(saida),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return CABECALHO + _ler_saida(kernel32, saida)


def decifrar(conteudo: bytes) -> bytes:
    if not conteudo.startswith(CABECALHO):
        raise ValueError("Arquivo de conexões não está no formato cifrado do hana-diag.")
    crypt32, kernel32 = _api()
    entrada, _b1 = _blob(conteudo[len(CABECALHO):])
    entropia, _b2 = _blob(_ENTROPIA)
    saida = _Blob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(entrada), None, ctypes.byref(entropia),
        None, None, _SEM_INTERFACE, ctypes.byref(saida),
    ):
        erro = ctypes.get_last_error()
        raise RuntimeError(
            "Não foi possível decifrar o cadastro de conexões. Ele só abre com o mesmo usuário do "
            f"Windows que o criou, nesta máquina (erro {erro}). Cadastre as bases de novo com hana-diag-config."
        )
    return _ler_saida(kernel32, saida)
