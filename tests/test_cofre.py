import json
import tempfile
import unittest
from pathlib import Path

from hana_diag_mcp import cofre, config
from hana_diag_mcp.config import Conexao, Configuracao

SEGREDOS = ["hana-cliente.interno", "ASTER_DIAG", "SBO_CLIENTE_PRD", "DIAG_CLIENTE_PRD"]


def _cfg():
    return Configuracao(conexoes=[Conexao(
        nome="cliente-prd", userkey="DIAG_CLIENTE_PRD", host="hana-cliente.interno", porta=30015,
        usuario="ASTER_DIAG", schema="SBO_CLIENTE_PRD", descricao="Cliente – produção",
    )])


class Cofre(unittest.TestCase):
    def test_ida_e_volta(self):
        dados = "acentuação e ç".encode("utf-8")
        self.assertEqual(cofre.decifrar(cofre.cifrar(dados)), dados)

    def test_adulterado_nao_abre(self):
        cifrado = bytearray(cofre.cifrar(b"segredo"))
        cifrado[-5] ^= 0xFF
        with self.assertRaises(RuntimeError):
            cofre.decifrar(bytes(cifrado))

    def test_texto_puro_e_recusado(self):
        with self.assertRaises(ValueError):
            cofre.decifrar(b'{"conexoes": []}')


class ArquivoDeConexoes(unittest.TestCase):
    def test_nada_legivel_no_disco(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "conexoes.dat"
            config.salvar(_cfg(), arq)
            bruto = arq.read_bytes()
            for segredo in SEGREDOS:
                with self.subTest(segredo=segredo):
                    self.assertNotIn(segredo.encode("utf-8"), bruto)
                    self.assertNotIn(segredo.encode("utf-16-le"), bruto)
            lido = config.carregar(arq).conexoes[0]
            self.assertEqual((lido.host, lido.usuario, lido.schema), ("hana-cliente.interno", "ASTER_DIAG", "SBO_CLIENTE_PRD"))
            self.assertFalse(arq.with_suffix(".tmp").exists())

    def test_migra_json_antigo_e_apaga_texto_puro(self):
        with tempfile.TemporaryDirectory() as d:
            legado = Path(d) / "conexoes.json"
            legado.write_text(json.dumps({"conexoes": [
                {"nome": "com-chave", "userkey": "DIAG_X", "schema": "SBO_X"},
                {"nome": "sem-chave", "host": "h", "usuario": "u", "senha_env": "SENHA"},
            ]}), encoding="utf-8")
            arq = Path(d) / "conexoes.dat"
            cfg = config.carregar(arq)
            self.assertEqual([c.nome for c in cfg.conexoes], ["com-chave"])
            self.assertFalse(legado.exists(), "o JSON em texto puro deveria ter sido apagado")
            self.assertTrue(arq.read_bytes().startswith(cofre.CABECALHO))

    def test_conexao_sem_chave_e_invalida(self):
        with self.assertRaises(ValueError):
            Conexao(nome="x", host="h", usuario="u").validar()


if __name__ == "__main__":
    unittest.main()
