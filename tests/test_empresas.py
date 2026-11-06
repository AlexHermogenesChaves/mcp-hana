import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hana_diag_mcp import config, configurar, empresas
from hana_diag_mcp.config import Conexao, Configuracao

SRGC = {
    "colunas": ["dbName", "cmpName", "versStr"],
    "linhas": [
        ["SBO_CLIENTE_PRD", "Cliente Brasil", "1000280"],
        ["SBO_CLIENTE_TST", "Cliente Teste", "1000280"],
        ["SBO_CLIENTE_SP", "Cliente SP", "1000280"],
    ],
}


def _base():
    return Conexao(nome="cliente-prd", userkey="DIAG_CLIENTE_PRD", host="10.0.0.15",
                   usuario="ASTER_DIAG", schema="SBO_CLIENTE_PRD", descricao="Cliente Brasil")


class Funcoes(unittest.TestCase):
    def test_sugerir_nome(self):
        self.assertEqual(empresas.sugerir_nome("SBO_CLIENTE_TST", set()), "cliente-tst")
        self.assertEqual(empresas.sugerir_nome("SBO_CLIENTE_TST", {"cliente-tst"}), "cliente-tst-2")
        self.assertEqual(empresas.sugerir_nome("EMPRESA X", set()), "empresa-x")

    def test_selecao(self):
        self.assertEqual(empresas.interpretar_selecao("1,3", 3), [0, 2])
        self.assertEqual(empresas.interpretar_selecao("3-2", 3), [1, 2])
        self.assertEqual(empresas.interpretar_selecao("todas", 3), [0, 1, 2])
        for ruim in ("4", "0", "a", "1-9"):
            with self.subTest(ruim=ruim), self.assertRaises(ValueError):
                empresas.interpretar_selecao(ruim, 3)

    def test_nova_conexao_reaproveita_credencial(self):
        c = empresas.nova_conexao(_base(), "cliente-tst", "SBO_CLIENTE_TST", "Cliente Teste")
        self.assertEqual((c.userkey, c.host, c.usuario), ("DIAG_CLIENTE_PRD", "10.0.0.15", "ASTER_DIAG"))
        self.assertEqual((c.nome, c.schema), ("cliente-tst", "SBO_CLIENTE_TST"))

    def test_fallback_sem_sbo_common(self):
        chamadas = []

        def executar(c, sql, parametros=(), max_linhas=200):
            chamadas.append(sql)
            if "SRGC" in sql:
                raise RuntimeError("insufficient privilege")
            return {"linhas": [["SBO_X", None, None]]}

        def executar_com_nome(c, sql, parametros=(), max_linhas=200):
            if "UNION ALL" in sql or '"SBO_X"."OADM"' in sql:
                chamadas.append(sql)
                return {"linhas": [["SBO_X", "Empresa X", None]]}
            return executar(c, sql, parametros, max_linhas)

        with mock.patch.object(empresas.banco, "executar", executar_com_nome):
            lista, origem = empresas.listar(_base())
        self.assertIn("OADM", chamadas[1])
        self.assertIn('FROM "SBO_X"."OADM"', chamadas[2])
        self.assertEqual(lista, [{"schema": "SBO_X", "nome": "Empresa X", "versao": ""}])
        self.assertIn("indisponível", origem)

    def test_fallback_sem_permissao_na_oadm_mantem_lista(self):
        def executar(c, sql, parametros=(), max_linhas=200):
            if "SRGC" in sql or '"SBO_X"."OADM"' in sql:
                raise RuntimeError("insufficient privilege")
            return {"linhas": [["SBO_X", None, None]]}

        with mock.patch.object(empresas.banco, "executar", executar):
            lista, _ = empresas.listar(_base())
        self.assertEqual(lista, [{"schema": "SBO_X", "nome": "", "versao": ""}])


class FluxoConfigurador(unittest.TestCase):
    def test_cadastra_escolhidas_sem_pedir_senha(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "conexoes.dat"
            config.salvar(Configuracao(conexoes=[_base()]), arq)
            respostas = iter(["2-3", "", "cliente-filial-sp", "n"])  # seleção, nome sugerido, nome próprio, não testar
            with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                 mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                 mock.patch.object(empresas.banco, "executar", lambda *a, **k: SRGC), \
                 mock.patch.object(configurar.subprocess, "run") as hdbuserstore, \
                 mock.patch("builtins.input", lambda *_: next(respostas)), \
                 contextlib.redirect_stdout(io.StringIO()) as saida:
                configurar.cadastrar_empresas("cliente-prd")
            hdbuserstore.assert_not_called()
            cfg = config.carregar(arq)
            self.assertEqual([c.nome for c in cfg.conexoes], ["cliente-prd", "cliente-tst", "cliente-filial-sp"])
            self.assertEqual({c.userkey for c in cfg.conexoes}, {"DIAG_CLIENTE_PRD"})
            self.assertEqual(cfg.conexoes[2].schema, "SBO_CLIENTE_SP")
            self.assertEqual(cfg.conexoes[2].descricao, "Cliente SP")
            self.assertIn("já cadastrada (cliente-prd)", saida.getvalue())

    def test_pula_empresa_ja_cadastrada(self):
        with tempfile.TemporaryDirectory() as d:
            arq = Path(d) / "conexoes.dat"
            config.salvar(Configuracao(conexoes=[_base()]), arq)
            respostas = iter(["1"])
            with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                 mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                 mock.patch.object(empresas.banco, "executar", lambda *a, **k: SRGC), \
                 mock.patch("builtins.input", lambda *_: next(respostas)), \
                 contextlib.redirect_stdout(io.StringIO()) as saida:
                configurar.cadastrar_empresas("cliente-prd")
            self.assertEqual(len(config.carregar(arq).conexoes), 1)
            self.assertIn("Nada novo", saida.getvalue())


if __name__ == "__main__":
    unittest.main()
