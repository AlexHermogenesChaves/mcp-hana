import unittest

from hana_diag_mcp import guarda
from hana_diag_mcp.guarda import ConsultaBloqueada, validar_somente_leitura

# Cópia exata da lista aprovada. Se este teste falhar, alguém mexeu nas palavras
# proibidas — e o instalar.ps1 recusa instalar até a lista voltar ao aprovado.
LISTA_APROVADA = [
    "ALTER", "ANNOTATE", "BACKUP", "BEGIN", "BULK", "CALL", "CHECKPOINT", "COMMENT", "COMMIT",
    "CONNECT", "CREATE", "DBCC", "DECLARE", "DELETE", "DENY", "DISCONNECT", "DO", "DROP", "EXEC",
    "EXECUTE", "EXPORT", "GRANT", "HOLDLOCK", "IMPORT", "INSERT", "INTO", "KILL", "LOAD", "LOCK",
    "MERGE", "NEXTVAL", "OPENDATASOURCE", "OPENQUERY", "OPENROWSET", "OPENXML", "RECONFIGURE",
    "RECOVER", "REFRESH", "RENAME", "RESTORE", "REVERT", "REVOKE", "ROLLBACK", "SAVEPOINT", "SET",
    "SETUSER", "SHUTDOWN", "TABLOCKX", "TRUNCATE", "UNLOAD", "UPDATE", "UPDATETEXT", "UPDLOCK",
    "UPSERT", "USE", "WAITFOR", "WRITETEXT", "XLOCK",
]

PERMITIDAS = {
    "select simples": "SELECT 1 FROM DUMMY",
    "CTE": 'WITH X AS (SELECT "ItemCode" FROM OITM) SELECT * FROM X',
    "CTE do T-SQL com ; inicial": ";WITH X AS (SELECT ItemCode FROM OITM) SELECT * FROM X",
    "; final": "SELECT 1 FROM DUMMY;  ",
    "; final + comentário": "SELECT 1 FROM DUMMY; -- fim",
    "proibida em literal": "SELECT * FROM OITM WHERE ItemName = 'DELETE; DROP TABLE OITM'",
    "proibida entre aspas": 'SELECT "U_Update", "Delete" FROM "@MINHATAB"',
    "proibida entre colchetes": "SELECT [Update], [Set] FROM [@MINHATAB]",
    "proibida em comentário": "-- não vou dar UPDATE\nSELECT 1 FROM DUMMY /* nem INSERT */",
    "função REPLACE": "SELECT REPLACE(ItemName, 'a', 'b') FROM OITM",
    "aspas escapadas": "SELECT 'it''s; DELETE' FROM DUMMY",
    "colchete escapado": "SELECT [a]]b] FROM T",
    "table function HANA": "SELECT * FROM \"SCH\".\"TF\"(P_INI => '20260101')",
    "TOP do T-SQL": "SELECT TOP 10 CardCode FROM OCRD ORDER BY CardCode",
    "paginação do T-SQL": "SELECT CardCode FROM OCRD ORDER BY CardCode OFFSET 0 ROWS FETCH NEXT 10 ROWS ONLY",
    "NOLOCK": "SELECT CardCode FROM OCRD WITH (NOLOCK)",
    "literal N''": "SELECT * FROM OCRD WHERE CardName LIKE N'%ÇÃO%'",
}

BLOQUEADAS = [
    "UPDATE OITM SET ItemName = 'x'",
    "insert into OITM values (1)",
    "DELETE FROM OITM",
    "MERGE INTO OITM USING X ON 1=1 WHEN MATCHED THEN DELETE",
    "UPSERT OITM VALUES (1) WITH PRIMARY KEY",
    "REPLACE OITM VALUES (1) WITH PRIMARY KEY",
    "TRUNCATE TABLE OITM",
    "DROP TABLE OITM",
    "CREATE TABLE X (A INT)",
    "ALTER TABLE OITM ADD (A INT)",
    "CALL SBO_SP_TRANSACTIONNOTIFICATION(1,2,3,4,5)",
    "DO BEGIN DELETE FROM OITM; END",
    "GRANT SELECT ON OITM TO X",
    "SET SCHEMA X",
    "COMMIT",
    "SELECT 1 FROM DUMMY; DELETE FROM OITM",
    "SELECT 1 FROM DUMMY;DELETE FROM OITM;",
    "SELECT * INTO X FROM OITM",
    "SELECT * FROM OITM FOR UPDATE",
    "SELECT * FROM OITM FOR SHARE LOCK",
    "WITH X AS (SELECT 1 A FROM DUMMY) INSERT INTO T SELECT * FROM X",
    "/* SELECT */ DELETE FROM OITM",
    "-- SELECT\nDELETE FROM OITM",
    "EXPLAIN PLAN FOR SELECT 1 FROM DUMMY",
    # T-SQL
    "SELECT 1 DELETE FROM OITM",
    "SELECT 1 EXEC xp_cmdshell 'dir'",
    "EXEC sp_who",
    "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'SELECT 1')",
    "SELECT * FROM OPENQUERY(LNK, 'SELECT 1')",
    "SELECT NEXT VALUE FOR dbo.Seq",
    'SELECT "SEQ".NEXTVAL FROM DUMMY',
    "SELECT * FROM OCRD WITH (UPDLOCK)",
    "SELECT * FROM OCRD WITH (XLOCK, ROWLOCK)",
    "SELECT 1 USE master",
    "SELECT 1 WAITFOR DELAY '00:10:00'",
    "SELECT 1 BEGIN TRAN",
    "SELECT 1 DECLARE @x INT",
    "SELECT 1 DBCC CHECKDB",
    "  ",
    "SELECT 'sem fechar FROM DUMMY",
    "SELECT 1 /* sem fechar",
    "SELECT [sem fechar FROM T",
]


class ListaFixa(unittest.TestCase):
    def test_lista_e_exatamente_a_aprovada(self):
        self.assertEqual(sorted(guarda.PALAVRAS_PROIBIDAS), LISTA_APROVADA)
        self.assertEqual(guarda.INICIOS_PERMITIDOS, frozenset({"SELECT", "WITH"}))

    def test_lista_interna_tambem_e_a_aprovada(self):
        self.assertEqual(sorted(guarda._PROIBIDAS), LISTA_APROVADA)
        self.assertEqual(guarda._INICIOS, frozenset({"SELECT", "WITH"}))

    def test_listas_sao_imutaveis(self):
        self.assertIsInstance(guarda.PALAVRAS_PROIBIDAS, frozenset)
        self.assertIsInstance(guarda._PROIBIDAS, frozenset)
        with self.assertRaises(AttributeError):
            guarda.PALAVRAS_PROIBIDAS.discard("DELETE")  # frozenset não tem discard

    def test_reatribuir_a_lista_publica_nao_libera_nada(self):
        original = guarda.PALAVRAS_PROIBIDAS
        try:
            guarda.PALAVRAS_PROIBIDAS = frozenset()
            with self.assertRaises(ConsultaBloqueada):
                validar_somente_leitura("SELECT 1 DELETE FROM OITM")
        finally:
            guarda.PALAVRAS_PROIBIDAS = original


class PermiteLeitura(unittest.TestCase):
    def test_permitidas(self):
        for nome, sql in PERMITIDAS.items():
            with self.subTest(nome):
                validar_somente_leitura(sql)

    def test_ponto_e_virgula_final_removido(self):
        self.assertEqual(validar_somente_leitura("SELECT 1 FROM DUMMY;  "), "SELECT 1 FROM DUMMY")

    def test_ponto_e_virgula_inicial_removido(self):
        self.assertEqual(validar_somente_leitura(";WITH X AS (SELECT 1 A) SELECT A FROM X"),
                         "WITH X AS (SELECT 1 A) SELECT A FROM X")


class BloqueiaEscrita(unittest.TestCase):
    def test_bloqueadas(self):
        for sql in BLOQUEADAS:
            with self.subTest(sql=sql), self.assertRaises(ConsultaBloqueada):
                validar_somente_leitura(sql)

    def test_mensagem_cita_a_palavra(self):
        with self.assertRaises(ConsultaBloqueada) as erro:
            validar_somente_leitura("SELECT 1 EXEC xp_cmdshell 'dir'")
        self.assertIn("EXEC", str(erro.exception))


if __name__ == "__main__":
    unittest.main()
