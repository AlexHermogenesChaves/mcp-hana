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

    def test_conexao_hana_sem_chave_nem_host_e_invalida(self):
        with self.assertRaises(ValueError):
            Conexao(nome="x").validar()

    def test_senha_do_sql_server_nao_fica_legivel(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "conexoes.dat"
            c = Conexao(nome="cli-sql", tipo="mssql", host="sql.cliente.interno", porta=1433,
                        banco="SBO_CLIENTE_PRD", usuario="LEITOR", senha="Senh@Secreta!9")
            config.salvar(Configuracao(conexoes=[c]), arq)
            bruto = arq.read_bytes()
            for segredo in ("Senh@Secreta!9", "sql.cliente.interno", "LEITOR"):
                self.assertNotIn(segredo.encode("utf-8"), bruto)
                self.assertNotIn(segredo.encode("utf-16-le"), bruto)
            self.assertEqual(config.carregar(arq).conexoes[0].senha, "Senh@Secreta!9")

    def test_le_registro_gravado_pela_versao_node(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "conexoes.dat"
            dados = {"conexoes": [{"nome": "n", "tipo": "mssql", "host": "h", "porta": 1433, "banco": "SBO_X",
                                   "usuario": "u", "senha": "s", "validarCertificado": True, "timeoutMs": 5000}],
                     "limites": {"linhasPadrao": 10}}
            arq.write_bytes(cofre.cifrar(json.dumps(dados).encode("utf-8")))
            c = config.carregar(arq).conexoes[0]
            self.assertEqual((c.tipo, c.validar_certificado, c.timeout_ms), ("mssql", True, 5000))

    def test_identificadores_perigosos_sao_recusados(self):
        for campo in ("schema", "banco", "instancia"):
            with self.subTest(campo=campo), self.assertRaises(ValueError):
                dados = {"nome": "x", "tipo": "mssql", "host": "h", "usuario": "u", "banco": "B", campo: "X]; DROP"}
                Conexao(**dados).validar()

    def test_resumo_nao_expoe_credencial(self):
        c = Conexao(nome="n", tipo="mssql", host="10.1.1.1", banco="SBO_X", usuario="LEITOR", senha="segredo")
        texto = json.dumps(c.resumo())
        for segredo in ("10.1.1.1", "LEITOR", "segredo"):
            self.assertNotIn(segredo, texto)
        self.assertEqual(c.resumo()["empresa"], "SBO_X")


if __name__ == "__main__":
    unittest.main()
