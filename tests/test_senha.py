import contextlib
import io
import subprocess
import unittest
from unittest import mock

from hana_diag_mcp import configurar
from hana_diag_mcp.config import Conexao

BASE = Conexao(nome="cliente-hmg", userkey="DIAG_CLIENTE_HMG", host="10.0.0.1", porta=30015, banco="NDB",
               usuario="LEITOR", schema="SBO_X")


def _ok(*args, **kwargs):
    return subprocess.CompletedProcess(args[0], 0, stdout="Password:Operation succeed.\n", stderr="")


class PedirSenha(unittest.TestCase):
    def test_confirma_e_informa_tamanho(self):
        respostas = iter(["abc", "abd", "", "s3nha!", "s3nha!"])  # diverge, vazia, confere
        with mock.patch.object(configurar, "_ler_oculto", lambda *_: next(respostas)), \
             contextlib.redirect_stdout(io.StringIO()) as saida:
            senha = configurar._pedir_senha("LEITOR")
        self.assertEqual(senha, "s3nha!")
        texto = saida.getvalue()
        self.assertIn("não conferem", texto)
        self.assertIn("senha vazia", texto)
        self.assertIn("senha recebida (6 caracteres)", texto)

    def test_desiste_depois_de_tres_tentativas(self):
        with mock.patch.object(configurar, "_ler_oculto", side_effect=["a", "b"] * 3), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            configurar._pedir_senha("LEITOR")


class GravarNoHdbuserstore(unittest.TestCase):
    def test_senha_vai_pela_entrada_padrao_nunca_no_argumento(self):
        with mock.patch.object(configurar, "_pedir_senha", return_value="s3nha!"), \
             mock.patch.object(configurar, "_hdbuserstore", return_value="hdbuserstore.exe"), \
             mock.patch.object(configurar.subprocess, "run", side_effect=_ok) as run, \
             contextlib.redirect_stdout(io.StringIO()):
            configurar._gravar_no_hdbuserstore("x", BASE)
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["hdbuserstore.exe", "-i", "SET", "DIAG_CLIENTE_HMG", "10.0.0.1:30015@NDB", "LEITOR"])
        self.assertNotIn("s3nha!", " ".join(args[0]))
        self.assertEqual(kwargs["input"], "s3nha!\n")

    def test_falha_do_hdbuserstore_interrompe(self):
        falha = subprocess.CompletedProcess([], 1, stdout="", stderr="ERROR 91112")
        with mock.patch.object(configurar, "_pedir_senha", return_value="x"), \
             mock.patch.object(configurar, "_hdbuserstore", return_value="hdbuserstore.exe"), \
             mock.patch.object(configurar.subprocess, "run", return_value=falha), \
             contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as erro:
            configurar._gravar_no_hdbuserstore("x", BASE)
        self.assertIn("91112", str(erro.exception))


if __name__ == "__main__":
    unittest.main()
