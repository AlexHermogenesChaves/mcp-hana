import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hana_diag_mcp import banco, config, configurar, empresas
from hana_diag_mcp.config import Conexao, Configuracao, mesmo_servidor


def _sql(**extra):
    dados = dict(nome="cli-prd", tipo="mssql", host="sql01", porta=1433, banco="SBO_CLI_PRD",
                 usuario="LEITOR", senha="p;w}d")
    dados.update(extra)
    return Conexao(**dados)


class StringDeConexao(unittest.TestCase):
    def test_driver_moderno(self):
        s = banco.string_conexao(_sql(), "ODBC Driver 18 for SQL Server")
        self.assertIn("DRIVER={ODBC Driver 18 for SQL Server}", s)
        self.assertIn("SERVER={sql01,1433}", s)
        self.assertIn("DATABASE={SBO_CLI_PRD}", s)
        self.assertIn("PWD={p;w}}d}", s)  # ';' protegido e '}' duplicado
        self.assertIn("Encrypt=no", s)
        self.assertIn("ApplicationIntent=ReadOnly", s)

    def test_instancia_nomeada_dispensa_porta(self):
        s = banco.string_conexao(_sql(instancia="SQLEXPRESS"), "ODBC Driver 17 for SQL Server")
        self.assertIn("SERVER={sql01\\SQLEXPRESS}", s)

    def test_driver_antigo_sem_tls(self):
        s = banco.string_conexao(_sql(), "SQL Server")
        self.assertNotIn("Encrypt", s)
        with self.assertRaises(RuntimeError):
            banco.string_conexao(_sql(encrypt=True), "SQL Server")

    def test_escolhe_o_melhor_driver(self):
        self.assertEqual(banco.driver_mssql(["SQL Server", "ODBC Driver 17 for SQL Server"]),
                         "ODBC Driver 17 for SQL Server")
        self.assertEqual(banco.driver_mssql(["SQL Server"]), "SQL Server")
        with self.assertRaises(RuntimeError):
            banco.driver_mssql(["HDBODBC"])

    def test_hana_com_chave_continua_igual(self):
        c = Conexao(nome="h", userkey="DIAG_H", schema="SBO_H", encrypt=True)
        s = banco.string_conexao(c)
        self.assertTrue(s.startswith("DRIVER=HDBODBC;KEY={DIAG_H}"))
        self.assertIn("CURRENTSCHEMA={SBO_H}", s)
        self.assertNotIn("PWD", s)

    def test_sem_senha_nao_conecta(self):
        with self.assertRaises(RuntimeError) as erro:
            banco.abrir(_sql(senha=""))
        self.assertIn("hana-diag-config senha cli-prd", str(erro.exception))

    def test_guarda_roda_antes_de_conectar(self):
        with mock.patch.object(banco, "abrir") as abrir, self.assertRaises(Exception):
            banco.executar(_sql(), "SELECT 1 EXEC xp_cmdshell 'dir'")
        abrir.assert_not_called()


class SessaoComRollback(unittest.TestCase):
    def test_sempre_rollback_e_sem_set_transaction(self):
        con = mock.MagicMock()
        cur = con.cursor.return_value
        cur.description = [("Usuario",), ("Empresa",)]
        cur.fetchmany.return_value = [("LEITOR", "SBO_CLI_PRD")]
        with mock.patch.object(banco, "abrir", return_value=con), mock.patch.object(banco, "_auditar"):
            r = banco.executar(_sql(), "SELECT SUSER_SNAME(), DB_NAME()")
        executados = [ch.args[0] for ch in cur.execute.call_args_list]
        self.assertEqual(executados, ["SELECT SUSER_SNAME(), DB_NAME()"])
        con.rollback.assert_called_once()
        con.close.assert_called_once()
        self.assertEqual(r["protecao_sessao"], "transação com rollback")
        self.assertEqual(r["linhas"], [["LEITOR", "SBO_CLI_PRD"]])


class Empresas(unittest.TestCase):
    def test_mesmo_servidor(self):
        self.assertTrue(mesmo_servidor(_sql(), _sql(nome="outra", banco="SBO_OUTRA")))
        self.assertFalse(mesmo_servidor(_sql(), _sql(host="sql02")))
        self.assertFalse(mesmo_servidor(_sql(), _sql(usuario="OUTRO")))
        self.assertFalse(mesmo_servidor(_sql(), Conexao(nome="h", host="sql01", usuario="LEITOR")))

    def test_nova_conexao_troca_o_banco(self):
        c = empresas.nova_conexao(_sql(), "cli-tst", "SBO_CLI_TST", "Teste")
        self.assertEqual((c.banco, c.senha, c.host), ("SBO_CLI_TST", "p;w}d", "sql01"))

    def test_sem_sbo_common_procura_bancos_com_oadm(self):
        def executar(c, sql, parametros=(), max_linhas=200):
            if "SRGC" in sql:
                raise RuntimeError("Invalid object name")
            if "sys.databases" in sql:
                return {"linhas": [["SBO_A"], ["OUTRO_SISTEMA"], ["X]; DROP"]]}
            if "[SBO_A].dbo.OADM" in sql:
                return {"linhas": [["Empresa A"]]}
            raise RuntimeError("Invalid object name OADM")

        with mock.patch.object(empresas.banco, "executar", executar):
            lista, origem = empresas.listar(_sql())
        self.assertEqual(lista, [{"schema": "SBO_A", "nome": "Empresa A", "versao": ""}])
        self.assertIn("bancos com OADM", origem)


class ConfiguradorSqlServer(unittest.TestCase):
    def _arquivo(self, d, conexoes=()):
        arq = Path(d) / "conexoes.dat"
        config.salvar(Configuracao(conexoes=list(conexoes)), arq)
        return arq

    def test_adicionar_guarda_senha_cifrada_sem_hdbuserstore(self):
        with tempfile.TemporaryDirectory() as d:
            arq = self._arquivo(d)
            respostas = iter(["mssql", "cli-prd", "Cliente PRD", "", "sql01", "", "1433", "SBO_CLI_PRD", "LEITOR", "n", "n"])
            with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                 mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                 mock.patch.object(configurar, "_pedir_senha", return_value="s3nha!"), \
                 mock.patch.object(configurar.subprocess, "run") as run, \
                 mock.patch("builtins.input", lambda *_: next(respostas)), \
                 contextlib.redirect_stdout(io.StringIO()):
                configurar.adicionar()
            run.assert_not_called()
            c = config.carregar(arq).obter("cli-prd")
            self.assertEqual((c.tipo, c.banco, c.senha, c.userkey), ("mssql", "SBO_CLI_PRD", "s3nha!", ""))
            self.assertNotIn(b"s3nha!", arq.read_bytes())

    def test_instancia_com_porta_vai_direto_pela_porta(self):
        with tempfile.TemporaryDirectory() as d:
            arq = self._arquivo(d)
            respostas = iter(["mssql", "cli-qa", "", "", "sql01", "QA", "50123", "SBO_CLI", "LEITOR", "n", "n"])
            with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                 mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                 mock.patch.object(configurar, "_pedir_senha", return_value="x"), \
                 mock.patch("builtins.input", lambda *_: next(respostas)), \
                 contextlib.redirect_stdout(io.StringIO()):
                configurar.adicionar()
            c = config.carregar(arq).obter("cli-qa")
            self.assertEqual((c.instancia, c.porta), ("", 50123))

    def test_portal_e_opcional_e_sem_portal_fica_vazio(self):
        with tempfile.TemporaryDirectory() as d:
            arq = self._arquivo(d)
            respostas = iter(["mssql", "cli-prd", "", "", "sql01", "", "1433", "SBO_CLI_PRD", "LEITOR", "n", "n"])
            with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                 mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                 mock.patch.object(configurar, "_pedir_senha", return_value="x"), \
                 mock.patch("builtins.input", lambda *_: next(respostas)), \
                 contextlib.redirect_stdout(io.StringIO()):
                configurar.adicionar()
            c = config.carregar(arq).obter("cli-prd")
            self.assertEqual(c.portal, "")
            self.assertEqual(c.resumo()["portal"], "")

    def test_portal_informado_no_cadastro_e_normalizado(self):
        with tempfile.TemporaryDirectory() as d:
            arq = self._arquivo(d)
            # 1º portal inválido (com usuário embutido) é recusado; o 2º, só o host, ganha https://
            respostas = iter([
                "mssql", "cli-prd", "", "https://alex:senha@portal.cli.com", "portal.cli.com/aster/",
                "sql01", "", "1433", "SBO_CLI_PRD", "LEITOR", "n", "n",
            ])
            with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                 mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                 mock.patch.object(configurar, "_pedir_senha", return_value="x"), \
                 mock.patch("builtins.input", lambda *_: next(respostas)), \
                 contextlib.redirect_stdout(io.StringIO()):
                configurar.adicionar()
            self.assertEqual(config.carregar(arq).obter("cli-prd").portal, "https://portal.cli.com/aster")

    def test_definir_portal_inclui_e_tira(self):
        with tempfile.TemporaryDirectory() as d:
            arq = self._arquivo(d, [_sql()])
            for resposta, esperado in (("https://aster.cli.com", "https://aster.cli.com"), ("-", "")):
                with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                     mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                     mock.patch("builtins.input", lambda *_, r=resposta: r), \
                     contextlib.redirect_stdout(io.StringIO()):
                    configurar.definir_portal("cli-prd")
                self.assertEqual(config.carregar(arq).obter("cli-prd").portal, esperado)

    def test_outra_empresa_do_servidor_nao_herda_o_portal(self):
        base = _sql(portal="https://aster.cli.com")
        self.assertEqual(empresas.nova_conexao(base, "cli-tst", "SBO_CLI_TST", "Teste").portal, "")

    def test_dica_quando_o_sql_browser_nao_responde(self):
        erro = "[08001] Erro ao Localizar Servidor/Instância Especificado [xFFFFFFFF]"
        dica = configurar._dica_de_erro(_sql(instancia="SERVIDOR01"), erro)
        self.assertIn("SQL Browser", dica)
        self.assertIn("porta 1433", dica)
        self.assertIn("senha cli-prd", configurar._dica_de_erro(_sql(), "Login failed for user"))

    def test_trocar_senha_oferece_o_mesmo_servidor(self):
        with tempfile.TemporaryDirectory() as d:
            arq = self._arquivo(d, [_sql(), _sql(nome="cli-tst", banco="SBO_CLI_TST"), _sql(nome="outro", host="sql02")])
            respostas = iter(["s", "n"])  # aplicar às do mesmo servidor, não testar
            with mock.patch.object(configurar, "carregar", lambda: config.carregar(arq)), \
                 mock.patch.object(configurar, "salvar", lambda cfg: config.salvar(cfg, arq)), \
                 mock.patch.object(configurar, "_pedir_senha", return_value="nova"), \
                 mock.patch("builtins.input", lambda *_: next(respostas)), \
                 contextlib.redirect_stdout(io.StringIO()):
                configurar.trocar_senha("cli-prd")
            senhas = {c.nome: c.senha for c in config.carregar(arq).conexoes}
            self.assertEqual(senhas, {"cli-prd": "nova", "cli-tst": "nova", "outro": "p;w}d"})


if __name__ == "__main__":
    unittest.main()
