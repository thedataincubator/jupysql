from unittest.mock import ANY
import uuid
import logging
import platform
import sqlite3
from decimal import Decimal
from pathlib import Path
import os.path
import re
import sys
import tempfile
import sqlalchemy
from textwrap import dedent
from unittest.mock import patch, Mock

import polars as pl
import pandas as pd
import pytest
from sqlalchemy import create_engine
from IPython.core.error import UsageError
from sql.connection import ConnectionManager
from sql.magic import SqlMagic, get_query_type
from sql.run.resultset import ResultSet
from sql import magic
from sql.warnings import JupySQLQuotedNamedParametersWarning


from conftest import runsql
from sql.connection import PLOOMBER_DOCS_LINK_STR
from ploomber_core.exceptions import COMMUNITY
import psutil

COMMUNITY = COMMUNITY.strip()

DISPLAYLIMIT_LINK = (
    '<a href="https://jupysql.ploomber.io/en/'
    'latest/api/configuration.html#displaylimit">displaylimit</a>'
)

SQLALCHEMY_VERSION = int(sqlalchemy.__version__.split(".")[0])


def test_memory_db(ip):
    assert runsql(ip, "SELECT * FROM test;")[0][0] == 1
    assert runsql(ip, "SELECT * FROM test;")[1][1] == "bar"


def test_html(ip):
    result = runsql(ip, "SELECT * FROM test;")
    assert "<td>foo</td>" in result._repr_html_().lower()


def test_print(ip):
    result = runsql(ip, "SELECT * FROM test;")
    assert re.search(r"1\s+\|\s+foo", str(result))


@pytest.mark.parametrize(
    "style, expected",
    [
        ("'PLAIN_COLUMNS'", r"1\s+foo"),
        ("'DEFAULT'", r" 1 \| foo  \|\n\|"),
        ("'SINGLE_BORDER'", r"│\n├───┼──────┤\n│ 1 │ foo  │\n│"),
        ("'MSWORD_FRIENDLY'", r"\n\| 1 \| foo  \|\n\|"),
    ],
)
def test_styles(ip, style, expected):
    ip.run_line_magic("config", f"SqlMagic.style = {style}")
    result = runsql(ip, "SELECT * FROM test;")
    assert re.search(expected, str(result))


@pytest.mark.skip
def test_multi_sql(ip):
    result = ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite://
        SELECT last_name FROM author;
        """,
    )
    assert "Shakespeare" in str(result) and "Brecht" in str(result)


def test_result_var(ip, capsys):
    ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite://
        x <<
        SELECT last_name FROM author;
        """,
    )
    result = ip.user_global_ns["x"]
    out, _ = capsys.readouterr()

    assert "Shakespeare" in str(result) and "Brecht" in str(result)
    assert "Returning data to local variable" not in out


def test_result_var_link(ip):
    ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite://
        x <<
        SELECT link FROM website;
        """,
    )
    result = ip.user_global_ns["x"]

    assert (
        "<a href=https://en.wikipedia.org/wiki/Bertolt_Brecht>"
        "https://en.wikipedia.org/wiki/Bertolt_Brecht</a>"
    ) in result._repr_html_()

    assert (
        "<a href=https://en.wikipedia.org/wiki/William_Shakespeare>"
        "https://en.wikipedia.org/wiki/William_Shakespeare</a>"
    ) in result._repr_html_()
    assert "<a href=google_link>google_link</a>" not in result._repr_html_()


def test_result_var_multiline_shovel(ip):
    ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite:// x << SELECT last_name
        FROM author;
        """,
    )
    result = ip.user_global_ns["x"]
    assert "Shakespeare" in str(result) and "Brecht" in str(result)


@pytest.mark.parametrize(
    "sql_statement, expected_result",
    [
        (
            """
            sqlite://
            x <<
            SELECT last_name FROM author;
            """,
            None,
        ),
        (
            """
            sqlite://
            x= <<
            SELECT last_name FROM author;
            """,
            {"last_name": ("Shakespeare", "Brecht")},
        ),
        (
            """
            sqlite://
            x = <<
            SELECT last_name FROM author;
            """,
            {"last_name": ("Shakespeare", "Brecht")},
        ),
        (
            """
            sqlite://
            x = <<
            SELECT last_name FROM author;
            """,
            {"last_name": ("Shakespeare", "Brecht")},
        ),
        (
            """
            sqlite://
            x =     <<
            SELECT last_name FROM author;
            """,
            {"last_name": ("Shakespeare", "Brecht")},
        ),
        (
            """
            sqlite://
            x      =     <<
            SELECT last_name FROM author;
            """,
            {"last_name": ("Shakespeare", "Brecht")},
        ),
    ],
)
def test_return_result_var(ip, sql_statement, expected_result):
    result = ip.run_cell_magic("sql", "", sql_statement)
    var = ip.user_global_ns["x"]
    assert "Shakespeare" in str(var) and "Brecht" in str(var)
    if result is not None:
        result = result.dict()
    assert result == expected_result


def test_access_results_by_keys(ip):
    assert runsql(ip, "SELECT * FROM author;")["William"] == (
        "William",
        "Shakespeare",
        1616,
    )


def test_duplicate_column_names_accepted(ip):
    result = ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite://
        SELECT last_name, last_name FROM author;
        """,
    )
    assert ("Brecht", "Brecht") in result


def test_persist_missing_pandas(ip, monkeypatch):
    monkeypatch.setattr(magic, "DataFrame", None)

    ip.run_cell("results = %sql SELECT * FROM test;")
    ip.run_cell("results_dframe = results.DataFrame()")

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql --persist sqlite:// results_dframe")

    assert excinfo.value.error_type == "MissingPackageError"
    assert "pip install pandas" in str(excinfo.value)


def test_persist(ip):
    runsql(ip, "")
    ip.run_cell("results = %sql SELECT * FROM test;")
    ip.run_cell("results_dframe = results.DataFrame()")
    ip.run_cell("%sql --persist sqlite:// results_dframe")
    persisted = runsql(ip, "SELECT * FROM results_dframe")
    assert persisted == [(0, 1, "foo"), (1, 2, "bar")]


def test_persist_in_schema(ip_empty):
    ip_empty.run_cell("%sql duckdb://")
    ip_empty.run_cell("%sql CREATE SCHEMA IF NOT EXISTS schema1;")
    df = pd.DataFrame({"a": [1, 2, 3]})
    ip_empty.push({"df": df})
    ip_empty.run_cell("%sql --persist schema1.df")
    persisted = ip_empty.run_cell("%sql SELECT * FROM schema1.df;").result.DataFrame()
    assert persisted["a"].tolist() == [1, 2, 3]


def test_persist_replace_in_schema(ip_empty):
    ip_empty.run_cell("%sql duckdb://")
    ip_empty.run_cell("%sql CREATE SCHEMA IF NOT EXISTS schema1;")
    df = pd.DataFrame({"a": [1, 2, 3]})
    ip_empty.push({"df": df})
    ip_empty.run_cell("%sql --persist schema1.df")
    df = pd.DataFrame({"a": [6, 7]})
    ip_empty.push({"df": df})
    ip_empty.run_cell("%sql --perist-replace schema1.df")
    persisted = ip_empty.run_cell("%sql SELECT * FROM schema1.df;").result.DataFrame()
    assert persisted["a"].tolist() == [1, 2, 3]


def test_append_in_schema(ip_empty):
    ip_empty.run_cell("%sql duckdb://")
    ip_empty.run_cell("%sql CREATE SCHEMA IF NOT EXISTS schema1;")
    df = pd.DataFrame({"a": [1, 2, 3]})
    ip_empty.push({"df": df})
    ip_empty.run_cell("%sql --persist schema1.df")
    df = pd.DataFrame({"a": [6, 7]})
    ip_empty.push({"df": df})
    ip_empty.run_cell("%sql --append schema1.df")
    persisted = ip_empty.run_cell("%sql SELECT * FROM schema1.df;").result.DataFrame()
    assert persisted["a"].tolist() == [1, 2, 3, 6, 7]


def test_persist_no_index(ip):
    runsql(ip, "")
    ip.run_cell("results = %sql SELECT * FROM test;")
    ip.run_cell("results_no_index = results.DataFrame()")
    ip.run_cell("%sql --persist sqlite:// results_no_index --no-index")
    persisted = runsql(ip, "SELECT * FROM results_no_index")
    assert persisted == [(1, "foo"), (2, "bar")]


@pytest.mark.parametrize(
    "sql_statement, expected_error",
    [
        ("%%sql --arg\n SELECT * FROM test", "Unrecognized argument(s): --arg"),
        ("%%sql -arg\n SELECT * FROM test", "Unrecognized argument(s): -arg"),
        ("%%sql --persist '--some' \n SELECT * FROM test", "not a valid identifier"),
    ],
)
def test_unrecognized_arguments_cell_magic(ip, sql_statement, expected_error):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell(sql_statement)

    assert expected_error in str(excinfo.value)


def test_ignore_argument_like_strings_if_they_come_after_the_sql_query(ip):
    assert ip.run_cell("%sql select * FROM test --some")


def test_persist_invalid_identifier(ip):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql --persist sqlite:// not an identifier")

    assert "not a valid identifier" in str(excinfo.value)


def test_persist_undefined_variable(ip):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql --persist sqlite:// not_a_variable")

    assert "Expected 'not_a_variable' to be a pd.DataFrame but it's undefined" in str(
        excinfo.value
    )


def test_persist_non_frame_raises(ip):
    ip.run_cell("not_a_dataframe = 22")

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql --persist sqlite:// not_a_dataframe")

    assert "is not a Pandas DataFrame or Series" in str(excinfo.value)


def test_append(ip):
    runsql(ip, "")
    ip.run_cell("results = %sql SELECT * FROM test;")
    ip.run_cell("results_dframe_append = results.DataFrame()")
    ip.run_cell("%sql --persist sqlite:// results_dframe_append")
    persisted = runsql(ip, "SELECT COUNT(*) FROM results_dframe_append")
    ip.run_cell("%sql --append sqlite:// results_dframe_append")
    appended = runsql(ip, "SELECT COUNT(*) FROM results_dframe_append")
    assert appended[0][0] == persisted[0][0] * 2


def test_persist_missing_argument(ip):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql --persist sqlite://")

    assert "Expected '' to be a pd.DataFrame but it's not a valid identifier" in str(
        excinfo.value
    )


def get_table_rows_as_dataframe(ip, table, name=None):
    """The function will generate the pandas dataframe in the namespace
    by querying the data by given table name"""
    if name:
        saved_df_name = name
    else:
        saved_df_name = f"df_{table}"
    ip.run_cell(f"results = %sql SELECT * FROM {table} LIMIT 1;")
    ip.run_cell(f"{saved_df_name} = results.DataFrame()")
    return saved_df_name


@pytest.mark.parametrize(
    "test_table, expected_result",
    [
        ("test", [(0, 1, "foo")]),
        ("author", [(0, "William", "Shakespeare", 1616)]),
        (
            "website",
            [
                (
                    0,
                    "Bertold Brecht",
                    "https://en.wikipedia.org/wiki/Bertolt_Brecht",
                    1954,
                )
            ],
        ),
        ("number_table", [(0, 4, -2)]),
    ],
)
def test_persist_replace_abbr_no_override(ip, test_table, expected_result):
    saved_df_name = get_table_rows_as_dataframe(ip, table=test_table)
    ip.run_cell(f"%sql -P sqlite:// {saved_df_name}")
    out = ip.run_cell(f"%sql SELECT * FROM {saved_df_name}")
    assert out.result == expected_result
    assert out.error_in_exec is None


@pytest.mark.parametrize(
    "test_table, expected_result",
    [
        ("test", [(0, 1, "foo")]),
        ("author", [(0, "William", "Shakespeare", 1616)]),
        (
            "website",
            [
                (
                    0,
                    "Bertold Brecht",
                    "https://en.wikipedia.org/wiki/Bertolt_Brecht",
                    1954,
                )
            ],
        ),
        ("number_table", [(0, 4, -2)]),
    ],
)
def test_persist_replace_no_override(ip, test_table, expected_result):
    saved_df_name = get_table_rows_as_dataframe(ip, table=test_table)
    ip.run_cell(f"%sql --persist-replace sqlite:// {saved_df_name}")
    out = ip.run_cell(f"%sql SELECT * FROM {saved_df_name}")
    assert out.result == expected_result
    assert out.error_in_exec is None


@pytest.mark.parametrize(
    "first_test_table, second_test_table, expected_result",
    [
        ("test", "author", [(0, "William", "Shakespeare", 1616)]),
        ("author", "test", [(0, 1, "foo")]),
        ("test", "number_table", [(0, 4, -2)]),
        ("number_table", "test", [(0, 1, "foo")]),
    ],
)
def test_persist_replace_override(
    ip, first_test_table, second_test_table, expected_result
):
    saved_df_name = "dummy_df_name"
    table_df = get_table_rows_as_dataframe(
        ip, table=first_test_table, name=saved_df_name
    )
    ip.run_cell(f"%sql --persist sqlite:// {table_df}")
    table_df = get_table_rows_as_dataframe(
        ip, table=second_test_table, name=saved_df_name
    )
    # To test the second --persist-replace executes successfully
    persist_replace_out = ip.run_cell(f"%sql --persist-replace sqlite:// {table_df}")
    assert persist_replace_out.error_in_exec is None

    # To test the persisted data is from --persist
    out = ip.run_cell(f"%sql SELECT * FROM {table_df}")
    assert out.result == expected_result
    assert out.error_in_exec is None


@pytest.mark.parametrize(
    "first_test_table, second_test_table, expected_result",
    [
        ("test", "author", [(0, 1, "foo")]),
        ("author", "test", [(0, "William", "Shakespeare", 1616)]),
        ("test", "number_table", [(0, 1, "foo")]),
        ("number_table", "test", [(0, 4, -2)]),
    ],
)
def test_persist_replace_override_reverted_order(
    ip, first_test_table, second_test_table, expected_result
):
    saved_df_name = "dummy_df_name"
    table_df = get_table_rows_as_dataframe(
        ip, table=first_test_table, name=saved_df_name
    )
    ip.run_cell(f"%sql --persist-replace sqlite:// {table_df}")
    table_df = get_table_rows_as_dataframe(
        ip, table=second_test_table, name=saved_df_name
    )

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell(f"%sql --persist sqlite:// {table_df}")

    # To test the second --persist executes not successfully
    assert (
        f"Table '{saved_df_name}' already exists. Consider using \
--persist-replace to drop the table before persisting the data frame"
        in str(excinfo.value)
    )

    # To test the persisted data is from --persist-replace
    out = ip.run_cell(f"%sql SELECT * FROM {table_df}")
    assert out.result == expected_result


@pytest.mark.parametrize(
    "test_table",
    [
        ("test"),
        ("author"),
        ("website"),
        ("number_table"),
    ],
)
def test_persist_and_append_use_together(ip, test_table):
    # Test error message when use --persist and --append together
    saved_df_name = get_table_rows_as_dataframe(ip, table=test_table)

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell(f"%sql --persist-replace --append sqlite:// {saved_df_name}")

    assert """You cannot simultaneously persist and append data to a dataframe;
                  please choose to utilize either one or the other.""" in str(
        excinfo.value
    )


@pytest.mark.parametrize(
    "test_table, expected_result",
    [
        ("test", [(0, 1, "foo")]),
        ("author", [(0, "William", "Shakespeare", 1616)]),
        (
            "website",
            [
                (
                    0,
                    "Bertold Brecht",
                    "https://en.wikipedia.org/wiki/Bertolt_Brecht",
                    1954,
                )
            ],
        ),
        ("number_table", [(0, 4, -2)]),
    ],
)
def test_persist_and_persist_replace_use_together(
    ip, capsys, test_table, expected_result
):
    # Test error message when use --persist and --persist-replace together
    saved_df_name = get_table_rows_as_dataframe(ip, table=test_table)
    # check UserWarning is raised
    with pytest.warns(UserWarning) as w:
        ip.run_cell(f"%sql --persist --persist-replace sqlite:// {saved_df_name}")

    # check that the message matches
    assert w[0].message.args[0] == "Please use either --persist or --persist-replace"

    # Test persist-replace is used
    execute_out = ip.run_cell(f"%sql SELECT * FROM {saved_df_name}")
    assert execute_out.result == expected_result
    assert execute_out.error_in_exec is None


@pytest.mark.parametrize(
    "first_test_table, second_test_table, expected_result",
    [
        ("test", "author", [(0, "William", "Shakespeare", 1616)]),
        ("author", "test", [(0, 1, "foo")]),
        ("test", "number_table", [(0, 4, -2)]),
        ("number_table", "test", [(0, 1, "foo")]),
    ],
)
def test_persist_replace_twice(
    ip, first_test_table, second_test_table, expected_result
):
    saved_df_name = "dummy_df_name"

    table_df = get_table_rows_as_dataframe(
        ip, table=first_test_table, name=saved_df_name
    )
    ip.run_cell(f"%sql --persist-replace sqlite:// {table_df}")

    table_df = get_table_rows_as_dataframe(
        ip, table=second_test_table, name=saved_df_name
    )
    ip.run_cell(f"%sql --persist-replace sqlite:// {table_df}")

    out = ip.run_cell(f"%sql SELECT * FROM {table_df}")
    # To test the persisted data is from --persist-replace
    assert out.result == expected_result
    assert out.error_in_exec is None


def test_connection_args_enforce_json(ip):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell('%sql --connection_arguments {"badlyformed":true')

    expected_message = "Expecting ',' delimiter"
    assert expected_message in str(excinfo.value)


@pytest.mark.skipif(platform.system() == "Windows", reason="failing on windows")
def test_connection_args_in_connection(ip):
    ip.run_cell('%sql --connection_arguments {"timeout":10} sqlite:///:memory:')
    result = ip.run_cell("%sql --connections")
    assert "timeout" in result.result["sqlite:///:memory:"].connect_args


@pytest.mark.skipif(platform.system() == "Windows", reason="failing on windows")
def test_connection_args_single_quotes(ip):
    ip.run_cell("%sql --connection_arguments '{\"timeout\": 10}' sqlite:///:memory:")
    result = ip.run_cell("%sql --connections")
    assert "timeout" in result.result["sqlite:///:memory:"].connect_args


def test_displaylimit_no_limit(ip):
    ip.run_line_magic("config", "SqlMagic.displaylimit = 0")

    out = ip.run_cell("%sql SELECT * FROM number_table;")
    assert out.result == [
        (4, -2),
        (-5, 0),
        (2, 4),
        (0, 2),
        (-5, -1),
        (-2, -3),
        (-2, -3),
        (-4, 2),
        (2, -5),
        (4, 3),
    ]


def test_displaylimit_default(ip):
    # Insert extra data to make number_table bigger (over 10 to see truncated string)
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")

    out = ip.run_cell("%sql SELECT * FROM number_table;").result

    assert f"Truncated to {DISPLAYLIMIT_LINK} of 10" in out._repr_html_()


def test_displaylimit(ip):
    ip.run_line_magic("config", "SqlMagic.autolimit = None")

    ip.run_line_magic("config", "SqlMagic.displaylimit = 1")
    result = runsql(ip, "SELECT * FROM author ORDER BY first_name;")

    assert "Brecht" in result._repr_html_()
    assert "Shakespeare" not in result._repr_html_()
    assert "Brecht" in repr(result)
    assert "Shakespeare" not in repr(result)


@pytest.mark.parametrize("config_value, expected_length", [(3, 3), (6, 6)])
def test_displaylimit_enabled_truncated_length(ip, config_value, expected_length):
    # Insert extra data to make number_table bigger (over 10 to see truncated string)
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")

    ip.run_cell(f"%config SqlMagic.displaylimit = {config_value}")
    out = runsql(ip, "SELECT * FROM number_table;")
    assert f"Truncated to {DISPLAYLIMIT_LINK} of {expected_length}" in out._repr_html_()


@pytest.mark.parametrize("config_value", [(None), (0)])
def test_displaylimit_enabled_no_limit(
    ip,
    config_value,
):
    # Insert extra data to make number_table bigger (over 10 to see truncated string)
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")

    ip.run_cell(f"%config SqlMagic.displaylimit = {config_value}")
    out = runsql(ip, "SELECT * FROM number_table;")
    assert "Truncated to displaylimit of " not in out._repr_html_()


@pytest.mark.parametrize(
    "config_value, expected_error_msg",
    [
        (-1, "displaylimit cannot be a negative integer"),
        (-2, "displaylimit cannot be a negative integer"),
        (-2.5, "The 'displaylimit' trait of a SqlMagic instance expected an int"),
        (
            "'some_string'",
            "The 'displaylimit' trait of a SqlMagic instance expected an int",
        ),
    ],
)
def test_displaylimit_enabled_with_invalid_values(
    ip, config_value, expected_error_msg, caplog
):
    with caplog.at_level(logging.ERROR):
        ip.run_cell(f"%config SqlMagic.displaylimit = {config_value}")

    assert expected_error_msg in caplog.text


@pytest.mark.parametrize(
    "query_clause, expected_truncated_length",
    [
        # With limit
        ("SELECT * FROM number_table", 12),
        ("SELECT * FROM number_table LIMIT 5", None),
        ("SELECT * FROM number_table LIMIT 10", None),
        ("SELECT * FROM number_table LIMIT 11", 11),
        # With conditions
        ("SELECT * FROM number_table WHERE x > 0", None),
        ("SELECT * FROM number_table WHERE x < 0", None),
        ("SELECT * FROM number_table WHERE y < 0", None),
        ("SELECT * FROM number_table WHERE y > 0", None),
    ],
)
@pytest.mark.parametrize("is_saved_by_cte", [(True, False)])
def test_displaylimit_with_conditional_clause(
    ip, query_clause, expected_truncated_length, is_saved_by_cte
):
    # Insert extra data to make number_table bigger (over 10 to see truncated string)
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")
    ip.run_cell("%sql INSERT INTO number_table VALUES (4, 3)")

    if is_saved_by_cte:
        ip.run_cell(f"%sql --save saved_cte --no-execute {query_clause}")
        out = ip.run_line_magic("sql", "--with saved_cte SELECT * from saved_cte")
    else:
        out = runsql(ip, query_clause)

    if expected_truncated_length:
        assert f"Truncated to {DISPLAYLIMIT_LINK} of 10" in out._repr_html_()


@pytest.mark.parametrize(
    "config_value",
    [
        (1),
        (0),
        (None),
    ],
)
def test_displaylimit_with_count_statement(ip, load_penguin, config_value):
    ip.run_cell(f"%config SqlMagic.displaylimit = {config_value}")
    result = ip.run_line_magic("sql", "select count(*) from penguins.csv")

    assert isinstance(result, ResultSet)
    assert str(result) == (
        "+--------------+\n"
        "| count_star() |\n"
        "+--------------+\n"
        "|     344      |\n"
        "+--------------+"
    )


def test_column_local_vars(ip):
    ip.run_line_magic("config", "SqlMagic.column_local_vars = True")
    result = runsql(ip, "SELECT * FROM author;")
    assert result is None
    assert "William" in ip.user_global_ns["first_name"]
    assert "Shakespeare" in ip.user_global_ns["last_name"]
    assert len(ip.user_global_ns["first_name"]) == 2
    ip.run_line_magic("config", "SqlMagic.column_local_vars = False")


def test_userns_not_changed(ip):
    ip.run_cell(
        dedent(
            """
    def function():
        local_var = 'local_val'
        %sql sqlite:// INSERT INTO test VALUES (2, 'bar');
    function()"""
        )
    )
    assert "local_var" not in ip.user_ns


def test_bind_vars(ip):
    ip.user_global_ns["x"] = 22
    result = runsql(ip, "SELECT {{x}}")
    assert result[0][0] == 22


def test_autopandas(ip):
    ip.run_line_magic("config", "SqlMagic.autopandas = True")
    dframe = runsql(ip, "SELECT * FROM test;")
    assert not dframe.empty
    assert dframe.ndim == 2
    assert dframe.name[0] == "foo"


def test_autopolars(ip):
    ip.run_line_magic("config", "SqlMagic.autopolars = True")
    dframe = runsql(ip, "SELECT * FROM test;")

    assert isinstance(dframe, pl.DataFrame)
    assert not dframe.is_empty()
    assert len(dframe.shape) == 2
    assert dframe["name"][0] == "foo"


def test_autopolars_infer_schema_length(ip):
    """Test for `SqlMagic.polars_dataframe_kwargs = {"infer_schema_length": None}`
    Without this config, polars will raise an exception when it cannot infer the
    correct schema from the first 100 rows.
    """
    # Create a table with 100 rows with a NULL value and one row with a non-NULL value
    ip.run_line_magic("config", "SqlMagic.autopolars = True")
    sql = ["CREATE TABLE test_autopolars_infer_schema (n INT, name TEXT)"]
    for i in range(100):
        sql.append(f"INSERT INTO test_autopolars_infer_schema VALUES ({i}, NULL)")
    sql.append("INSERT INTO test_autopolars_infer_schema VALUES (100, 'foo')")
    runsql(ip, sql)

    # By default, this dataset should raise a ComputeError
    with pytest.raises(pl.exceptions.ComputeError):
        runsql(ip, "SELECT * FROM test_autopolars_infer_schema;")

    # To avoid this error, pass the `infer_schema_length` argument to polars.DataFrame
    line_magic = 'SqlMagic.polars_dataframe_kwargs = {"infer_schema_length": None}'
    ip.run_line_magic("config", line_magic)
    dframe = runsql(ip, "SELECT * FROM test_autopolars_infer_schema;")
    assert dframe.schema == {"n": pl.Int64, "name": pl.Utf8}

    # Assert that if we unset the dataframe kwargs, the error is raised again
    ip.run_line_magic("config", "SqlMagic.polars_dataframe_kwargs = {}")
    with pytest.raises(pl.exceptions.ComputeError):
        runsql(ip, "SELECT * FROM test_autopolars_infer_schema;")

    runsql(ip, "DROP TABLE test_autopolars_infer_schema")


def test_mutex_autopolars_autopandas(ip):
    ip.run_line_magic("config", "SqlMagic.autopolars = False")
    ip.run_line_magic("config", "SqlMagic.autopandas = False")

    dframe = runsql(ip, "SELECT * FROM test;")
    assert isinstance(dframe, ResultSet)

    ip.run_line_magic("config", "SqlMagic.autopolars = True")
    dframe = runsql(ip, "SELECT * FROM test;")
    assert isinstance(dframe, pl.DataFrame)

    ip.run_line_magic("config", "SqlMagic.autopandas = True")
    dframe = runsql(ip, "SELECT * FROM test;")
    assert isinstance(dframe, pd.DataFrame)

    # Test that re-enabling autopolars works
    ip.run_line_magic("config", "SqlMagic.autopolars = True")
    dframe = runsql(ip, "SELECT * FROM test;")
    assert isinstance(dframe, pl.DataFrame)

    # Disabling autopolars at this point should result in the default behavior
    ip.run_line_magic("config", "SqlMagic.autopolars = False")
    dframe = runsql(ip, "SELECT * FROM test;")
    assert isinstance(dframe, ResultSet)


def test_csv(ip):
    ip.run_line_magic("config", "SqlMagic.autopandas = False")  # uh-oh
    result = runsql(ip, "SELECT * FROM test;")
    result = result.csv()
    for row in result.splitlines():
        assert row.count(",") == 1
    assert len(result.splitlines()) == 3


def test_csv_to_file(ip):
    ip.run_line_magic("config", "SqlMagic.autopandas = False")  # uh-oh
    result = runsql(ip, "SELECT * FROM test;")
    with tempfile.TemporaryDirectory() as tempdir:
        fname = os.path.join(tempdir, "test.csv")
        output = result.csv(fname)
        assert os.path.exists(output.file_path)
        with open(output.file_path) as csvfile:
            content = csvfile.read()
            for row in content.splitlines():
                assert row.count(",") == 1
            assert len(content.splitlines()) == 3


def test_sql_from_file(ip):
    ip.run_line_magic("config", "SqlMagic.autopandas = False")
    with tempfile.TemporaryDirectory() as tempdir:
        fname = os.path.join(tempdir, "test.sql")
        with open(fname, "w") as tempf:
            tempf.write("SELECT * FROM test;")
        result = ip.run_cell("%sql --file " + fname)
        assert result.result == [(1, "foo"), (2, "bar")]


def test_sql_from_nonexistent_file(ip):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql --file some_file_that_doesnt_exist.sql")

    assert "No such file or directory: 'some_file_that_doesnt_exist.sql" in str(
        excinfo.value
    )
    assert excinfo.value.error_type == "FileNotFoundError"


def test_dict(ip):
    result = runsql(ip, "SELECT * FROM author;")
    result = result.dict()
    assert isinstance(result, dict)
    assert "first_name" in result
    assert "last_name" in result
    assert "year_of_death" in result
    assert len(result["last_name"]) == 2


def test_dicts(ip):
    result = runsql(ip, "SELECT * FROM author;")
    for row in result.dicts():
        assert isinstance(row, dict)
        assert "first_name" in row
        assert "last_name" in row
        assert "year_of_death" in row


def test_bracket_var_substitution(ip):
    ip.user_global_ns["col"] = "first_name"
    assert runsql(ip, "SELECT * FROM author" " WHERE {{col}} = 'William' ")[0] == (
        "William",
        "Shakespeare",
        1616,
    )

    ip.user_global_ns["col"] = "last_name"
    result = runsql(ip, "SELECT * FROM author" " WHERE {{col}} = 'William' ")
    assert not result


# the next two tests had the same name, so I added a _2 to the second one
def test_multiline_bracket_var_substitution(ip):
    ip.user_global_ns["col"] = "first_name"
    assert runsql(ip, "SELECT * FROM author\n" " WHERE {{col}} = 'William' ")[0] == (
        "William",
        "Shakespeare",
        1616,
    )

    ip.user_global_ns["col"] = "last_name"
    result = runsql(ip, "SELECT * FROM author" " WHERE {{col}} = 'William' ")
    assert not result


def test_multiline_bracket_var_substitution_2(ip):
    ip.user_global_ns["col"] = "first_name"
    result = ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite:// SELECT * FROM author
        WHERE {{col}} = 'William'
        """,
    )
    assert ("William", "Shakespeare", 1616) in result

    ip.user_global_ns["col"] = "last_name"
    result = ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite:// SELECT * FROM author
        WHERE {{col}} = 'William'
        """,
    )
    assert not result


def test_json_in_select(ip):
    # Variable expansion does not work within json, but
    # at least the two usages of curly braces do not collide
    ip.user_global_ns["person"] = "prince"
    result = ip.run_cell_magic(
        "sql",
        "",
        """
        sqlite://
        SELECT
          '{"greeting": "Farewell sweet {person}"}'
        AS json
        """,
    )

    assert result == [('{"greeting": "Farewell sweet {person}"}',)]


def test_closed_connections_are_no_longer_listed(ip):
    connections = runsql(ip, "%sql -l")
    connection_name = list(connections)[0]
    runsql(ip, f"%sql -x {connection_name}")
    connections_afterward = runsql(ip, "%sql -l")
    assert connection_name not in connections_afterward


def test_close_connection(ip, tmp_empty):
    process = psutil.Process()

    ip.run_cell("%sql sqlite:///one.db")
    ip.run_cell("%sql sqlite:///two.db")

    # check files are open
    assert {Path(f.path).name for f in process.open_files()} >= {"one.db", "two.db"}

    # close connections
    ip.run_cell("%sql -x sqlite:///one.db")
    ip.run_cell("%sql --close sqlite:///two.db")

    # connections should not longer appear
    assert "sqlite:///one.db" not in ConnectionManager.connections
    assert "sqlite:///two.db" not in ConnectionManager.connections

    # files should be closed
    assert {Path(f.path).name for f in process.open_files()} & {
        "one.db",
        "two.db",
    } == set()


@pytest.mark.parametrize(
    "close_cell",
    [
        "%sql -x first",
        "%sql --close first",
    ],
)
def test_close_connection_with_alias(ip, tmp_empty, close_cell):
    process = psutil.Process()

    ip.run_cell("%sql sqlite:///one.db --alias first")

    assert {Path(f.path).name for f in process.open_files()} >= {"one.db"}

    ip.run_cell(close_cell)

    assert "sqlite:///one.db" not in ConnectionManager.connections
    assert "first" not in ConnectionManager.connections
    assert "one.db" not in {Path(f.path).name for f in process.open_files()}


def test_alias(clean_conns, ip_empty, tmp_empty):
    ip_empty.run_cell("%sql sqlite:///one.db --alias one")
    assert {"one"} == set(ConnectionManager.connections)


def test_alias_existing_engine(clean_conns, ip_empty, tmp_empty):
    ip_empty.user_global_ns["first"] = create_engine("sqlite:///first.db")
    ip_empty.run_cell("%sql first --alias one")
    assert {"one"} == set(ConnectionManager.connections)


def test_alias_dbapi_connection(clean_conns, ip_empty, tmp_empty):
    ip_empty.user_global_ns["first"] = create_engine("sqlite://")
    ip_empty.run_cell("%sql first --alias one")
    assert {"one"} == set(ConnectionManager.connections)


def test_close_connection_with_existing_engine_and_alias(ip, tmp_empty):
    ip.user_global_ns["first"] = create_engine("sqlite:///first.db")
    ip.user_global_ns["second"] = create_engine("sqlite:///second.db")

    # open two connections
    ip.run_cell("%sql first --alias one")
    ip.run_cell("%sql second --alias two")

    # close them
    ip.run_cell("%sql -x one")
    ip.run_cell("%sql --close two")

    assert "sqlite:///first.db" not in ConnectionManager.connections
    assert "sqlite:///second.db" not in ConnectionManager.connections
    assert "first" not in ConnectionManager.connections
    assert "second" not in ConnectionManager.connections


def test_close_connection_with_dbapi_connection_and_alias(ip, tmp_empty):
    ip.user_global_ns["first"] = create_engine("sqlite:///first.db")
    ip.user_global_ns["second"] = create_engine("sqlite:///second.db")

    # open two connections
    ip.run_cell("%sql first --alias one")
    ip.run_cell("%sql second --alias two")

    # close them
    ip.run_cell("%sql -x one")
    ip.run_cell("%sql --close two")

    assert "sqlite:///first.db" not in ConnectionManager.connections
    assert "sqlite:///second.db" not in ConnectionManager.connections
    assert "first" not in ConnectionManager.connections
    assert "second" not in ConnectionManager.connections


def test_creator_no_argument_raises(ip_empty):
    with pytest.raises(
        UsageError, match="argument -c/--creator: expected one argument"
    ):
        ip_empty.run_line_magic("sql", "--creator")


def test_creator(monkeypatch, ip_empty):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///")

    def creator():
        return sqlite3.connect("")

    ip_empty.user_global_ns["func"] = creator
    ip_empty.run_line_magic("sql", "--creator func")

    result = ip_empty.run_line_magic(
        "sql", "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name;"
    )

    assert isinstance(result, ResultSet)


def test_column_names_visible(ip, tmp_empty):
    res = ip.run_line_magic("sql", "SELECT * FROM empty_table")

    assert "<th>column</th>" in res._repr_html_()
    assert "<th>another</th>" in res._repr_html_()


@pytest.mark.xfail(reason="known parse @ parser.py error")
def test_sqlite_path_with_spaces(ip, tmp_empty):
    ip.run_cell("%sql sqlite:///some database.db")

    assert Path("some database.db").is_file()


def test_pass_existing_engine(ip, tmp_empty):
    ip.user_global_ns["my_engine"] = create_engine("sqlite:///my.db")
    ip.run_line_magic("sql", "  my_engine ")

    runsql(
        ip,
        [
            "CREATE TABLE some_data (n INT, name TEXT)",
            "INSERT INTO some_data VALUES (10, 'foo')",
            "INSERT INTO some_data VALUES (20, 'bar')",
        ],
    )

    result = ip.run_line_magic("sql", "SELECT * FROM some_data")

    assert result == [(10, "foo"), (20, "bar")]


# there's some weird shared state with this one, moving it to the end
def test_autolimit(ip):
    # test table has two rows
    ip.run_line_magic("config", "SqlMagic.autolimit = 0")
    result = runsql(ip, "SELECT * FROM test;")
    assert len(result) == 2

    # test table has two rows
    ip.run_line_magic("config", "SqlMagic.autolimit = None")
    result = runsql(ip, "SELECT * FROM test;")
    assert len(result) == 2

    # test setting autolimit to 1
    ip.run_line_magic("config", "SqlMagic.autolimit = 1")
    result = runsql(ip, "SELECT * FROM test;")
    assert len(result) == 1


invalid_connection_string = f"""
No active connection.

To fix it:

Pass a valid connection string:
    Example: %sql postgresql://username:password@hostname/dbname

OR

Set the environment variable $DATABASE_URL

For more details, see: {PLOOMBER_DOCS_LINK_STR}
{COMMUNITY}
"""


def test_error_on_invalid_connection_string(ip_empty, clean_conns):
    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql some invalid connection string")

    assert invalid_connection_string.strip() == str(excinfo.value)


invalid_connection_string_format = f"""\
Can't load plugin: sqlalchemy.dialects:something

To fix it, make sure you are using correct driver name:
Ref: https://docs.sqlalchemy.org/en/20/core/engines.html#database-urls

For more details, see: {PLOOMBER_DOCS_LINK_STR}
{COMMUNITY}
"""  # noqa


def test_error_on_invalid_connection_string_format(ip_empty, clean_conns):
    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql something://")

    assert invalid_connection_string_format.strip() == str(excinfo.value)


def test_error_on_invalid_connection_string_with_existing_conns(ip_empty, clean_conns):
    ip_empty.run_cell("%sql sqlite://")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql something://")

    assert invalid_connection_string_format.strip() == str(excinfo.value)


invalid_connection_string_with_possible_typo = f"""
Can't load plugin: sqlalchemy.dialects:sqlit

Perhaps you meant to use driver the dialect: "sqlite"

For more details, see: {PLOOMBER_DOCS_LINK_STR}
{COMMUNITY}
"""  # noqa


def test_error_on_invalid_connection_string_with_possible_typo(ip_empty, clean_conns):
    ip_empty.run_cell("%sql sqlite://")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql sqlit://")

    assert invalid_connection_string_with_possible_typo.strip() == str(excinfo.value)


invalid_connection_string_duckdb_top = """
An error happened while creating the connection: connect(): incompatible function arguments. The following argument types are supported:
    1. (database: str = ':memory:', read_only: bool = False, config: dict = None) -> duckdb.DuckDBPyConnection
"""  # noqa

invalid_connection_string_duckdb_bottom = f"""
Perhaps you meant to use the 'duckdb' db 
To find more information regarding connection: https://jupysql.ploomber.io/en/latest/integrations/duckdb.html

To fix it:

Pass a valid connection string:
    Example: %sql postgresql://username:password@hostname/dbname

For more details, see: {PLOOMBER_DOCS_LINK_STR}
{COMMUNITY}
"""  # noqa


def test_error_on_invalid_connection_string_duckdb(ip_empty, clean_conns):
    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql duckdb://invalid_db")

    assert invalid_connection_string_duckdb_top.strip() in str(excinfo.value)
    assert invalid_connection_string_duckdb_bottom.strip() in str(excinfo.value)


@pytest.mark.parametrize(
    "establish_non_identifier, non_identifier",
    [
        (
            "conn_in_lst = [conn]",
            "conn_in_lst[0]",
        ),
        (
            "conn_in_dict = {'conn1': conn}",
            "conn_in_dict['conn1']",
        ),
        (
            """
class ConnInObj(object):
    def __init__(self, conn):
        self.conn1 = conn

conn_in_obj = ConnInObj(conn)
""",
            "conn_in_obj.conn1",
        ),
    ],
)
def test_error_on_passing_non_identifier_to_connect(
    ip_empty, establish_non_identifier, non_identifier
):
    ip_empty.run_cell("import duckdb; conn = duckdb.connect();")
    ip_empty.run_cell(establish_non_identifier)

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell(f"%sql {non_identifier}")

    assert excinfo.value.error_type == "UsageError"
    assert (
        f"'{non_identifier}' is not a valid connection identifier. "
        "Please pass the variable's name directly, as passing "
        "object attributes, dictionaries or lists won't work."
    ) in str(excinfo.value)


@pytest.mark.skipif(
    SQLALCHEMY_VERSION == 1, reason="no transaction is active error with sqlalchemy 1.x"
)
@pytest.mark.parametrize(
    "command",
    [
        ("commit;"),
        ("rollback;"),
    ],
)
def test_passing_command_ending_with_semicolon(ip_empty, command):
    expected_result = "+---------+\n" "| Success |\n" "+---------+\n" "+---------+"
    ip_empty.run_cell("%sql duckdb://")

    out = ip_empty.run_cell(f"%sql {command}").result
    assert str(out) == expected_result

    ip_empty.run_cell(
        f"""%%sql
{command}
"""
    )
    assert str(out) == expected_result


def test_jupysql_alias():
    assert SqlMagic.magics == {
        "line": {"jupysql": "execute", "sql": "execute"},
        "cell": {"jupysql": "execute", "sql": "execute"},
    }


@pytest.mark.xfail(reason="will be fixed once we deprecate the $name parametrization")
def test_columns_with_dollar_sign(ip_empty):
    ip_empty.run_cell("%sql sqlite://")
    result = ip_empty.run_cell(
        """
    %sql SELECT $2 FROM (VALUES (1, 'one'), (2, 'two'), (3, 'three'))"""
    )

    html = result.result._repr_html_()

    assert "$2" in html


def test_save_with(ip):
    # First Query
    ip.run_cell(
        "%sql --save shakespeare SELECT * FROM author WHERE last_name = 'Shakespeare'"
    )
    # Second Query
    ip.run_cell(
        "%sql --with shakespeare --save shake_born_in_1616 SELECT * FROM "
        "shakespeare WHERE year_of_death = 1616"
    )

    # Third Query
    ip.run_cell(
        "%sql --save shake_born_in_1616_limit_10 --with shake_born_in_1616"
        " SELECT * FROM shake_born_in_1616 LIMIT 10"
    )

    second_out = ip.run_cell(
        "%sql --with shake_born_in_1616 SELECT * FROM shake_born_in_1616"
    )
    third_out = ip.run_cell(
        "%sql --with shake_born_in_1616_limit_10"
        " SELECT * FROM shake_born_in_1616_limit_10"
    )
    assert second_out.result == [("William", "Shakespeare", 1616)]
    assert third_out.result == [("William", "Shakespeare", 1616)]


@pytest.mark.parametrize(
    "prep_cell_1, prep_cell_2, prep_cell_3, with_cell_1,"
    " with_cell_2, with_cell_1_excepted, with_cell_2_excepted",
    [
        [
            "%sql --save everything SELECT * FROM number_table",
            "%sql --with everything --no-execute --save positive_x"
            " SELECT * FROM everything WHERE x > 0",
            "%sql --with positive_x --no-execute --save "
            "positive_x_and_y SELECT * FROM positive_x WHERE y > 0",
            "%sql --with positive_x SELECT * FROM positive_x",
            "%sql --with positive_x_and_y SELECT * FROM positive_x_and_y",
            [(4, -2), (2, 4), (2, -5), (4, 3)],
            [(2, 4), (4, 3)],
        ],
        [
            "%sql --save everything SELECT * FROM number_table",
            "%sql --with everything --no-execute --save odd_x "
            "SELECT * FROM everything WHERE x % 2 != 0",
            "%sql --with odd_x --no-execute --save odd_x_and_y "
            "SELECT * FROM odd_x WHERE y % 2 != 0",
            "%sql --with odd_x SELECT * FROM odd_x",
            "%sql --with odd_x_and_y SELECT * FROM odd_x_and_y",
            [(-5, 0), (-5, -1)],
            [(-5, -1)],
        ],
    ],
)
def test_save_with_number_table(
    ip,
    prep_cell_1,
    prep_cell_2,
    prep_cell_3,
    with_cell_1,
    with_cell_2,
    with_cell_1_excepted,
    with_cell_2_excepted,
):
    ip.run_cell(prep_cell_1)
    ip.run_cell(prep_cell_2)
    ip.run_cell(prep_cell_3)
    ip.run_cell(prep_cell_1)

    with_cell_1_out = ip.run_cell(with_cell_1).result
    with_cell_2_out = ip.run_cell(with_cell_2).result
    assert with_cell_1_excepted == with_cell_1_out
    assert with_cell_2_excepted == with_cell_2_out


def test_save_with_non_existing_with(ip):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell(
            "%sql --with non_existing_sub_query SELECT * FROM non_existing_sub_query"
        )

    assert '"non_existing_sub_query" is not a valid snippet identifier.' in str(
        excinfo.value
    )
    assert excinfo.value.error_type == "UsageError"


def test_save_with_non_existing_table(ip):
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql --save my_query SELECT * FROM non_existing_table")

    assert excinfo.value.error_type == "RuntimeError"
    assert "(sqlite3.OperationalError) no such table: non_existing_table" in str(
        excinfo.value
    )


def test_interact_basic_data_types(ip, capsys):
    ip.user_global_ns["my_variable"] = 5
    ip.run_cell(
        "%sql --interact my_variable SELECT * FROM author LIMIT {{my_variable}}"
    )
    out, _ = capsys.readouterr()

    assert (
        "Interactive mode, please interact with below widget(s)"
        " to control the variable" in out
    )


@pytest.fixture
def mockValueWidget(monkeypatch):
    with patch("ipywidgets.widgets.IntSlider") as MockClass:
        instance = MockClass.return_value
        yield instance


def test_interact_basic_widgets(ip, mockValueWidget, capsys):
    ip.user_global_ns["my_widget"] = mockValueWidget

    ip.run_cell(
        "%sql --interact my_widget SELECT * FROM number_table LIMIT {{my_widget}}"
    )
    out, _ = capsys.readouterr()
    assert (
        "Interactive mode, please interact with below widget(s)"
        " to control the variable" in out
    )


def test_interact_and_missing_ipywidgets_installed(ip):
    with patch.dict(sys.modules):
        sys.modules["ipywidgets"] = None
        ip.user_global_ns["my_variable"] = 5

        with pytest.raises(ModuleNotFoundError) as excinfo:
            ip.run_cell(
                "%sql --interact my_variable SELECT * FROM author LIMIT {{my_variable}}"
            )

    assert "'ipywidgets' is required to use '--interactive argument'" in str(
        excinfo.value
    )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "ip",
        "ip_dbapi",
    ],
)
def test_interpolation_ignore_literals(fixture_name, request):
    ip = request.getfixturevalue(fixture_name)

    ip.run_cell("%config SqlMagic.named_parameters = True")

    # this isn't a parameter because it's quoted (':last_name')
    result = ip.run_cell(
        "%sql select * from author where last_name = ':last_name'"
    ).result
    assert result.dict() == {}


def test_sqlalchemy_interpolation(ip):
    ip.run_cell("%config SqlMagic.named_parameters = True")

    ip.run_cell("last_name = 'Shakespeare'")

    # define another variable to ensure the test doesn't break if there are more
    # variables in the namespace
    ip.run_cell("first_name = 'William'")

    result = ip.run_cell(
        "%sql select * from author where last_name = :last_name"
    ).result

    assert result.dict() == {
        "first_name": ("William",),
        "last_name": ("Shakespeare",),
        "year_of_death": (1616,),
    }


def test_sqlalchemy_interpolation_missing_parameter(ip):
    ip.run_cell("%config SqlMagic.named_parameters = True")

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql select * from author where last_name = :last_name")

    assert (
        "Cannot execute query because the following variables are undefined: last_name"
        in str(excinfo.value)
    )


@pytest.mark.parametrize(
    "fixture_name",
    [
        "ip",
        "ip_dbapi",
    ],
)
def test_sqlalchemy_insert_literals_with_colon_character(fixture_name, request):
    ip = request.getfixturevalue(fixture_name)

    ip.run_cell(
        """%%sql
CREATE TABLE names (
    name VARCHAR(50) NOT NULL
);

INSERT INTO names (name)
VALUES
    ('John'),
    (':Mary'),
    ('Alex'),
    (':Lily'),
    ('Michael'),
    ('Robert'),
    (':Sarah'),
    ('Jennifer'),
    (':Tom'),
    ('Jessica');
"""
    )

    result = ip.run_cell("%sql SELECT * FROM names WHERE name = ':Mary'").result

    assert result.dict() == {"name": (":Mary",)}


def test_error_suggests_turning_feature_on_if_it_detects_named_params(ip):
    ip.run_cell("%config SqlMagic.named_parameters = False")

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell("%sql SELECT * FROM penguins.csv where species = :species")

    suggestion = (
        "Your query contains named parameters (species) "
        'but the named parameters feature is "warn". \nEnable it '
        'with: %config SqlMagic.named_parameters="enabled" \nor '
        "disable it with: "
        '%config SqlMagic.named_parameters="disabled"\n'
        "For more info, see the docs: "
        "https://jupysql.ploomber.io/en/latest/api/configuration.html"
    )
    assert suggestion in str(excinfo.value)


@pytest.mark.parametrize(
    "cell, expected_warning",
    [
        (
            "%sql SELECT * FROM author where last_name = ':last_name'",
            "The following variables are defined: last_name.",
        ),
        (
            "%sql SELECT * FROM author where last_name = ':last_name' "
            "and first_name = :first_name",
            "The following variables are defined: last_name.",
        ),
        (
            "%sql SELECT * FROM author where last_name = ':last_name' "
            "and first_name = ':first_name'",
            "The following variables are defined: first_name, last_name.",
        ),
    ],
    ids=[
        "one-quoted",
        "one-quoted-one-unquoted",
        "two-quoted",
    ],
)
def test_warning_if_variable_defined_but_named_param_is_quoted(
    ip, cell, expected_warning
):
    ip.run_cell("%config SqlMagic.named_parameters = True")
    ip.run_cell("last_name = 'Shakespeare'")
    ip.run_cell("first_name = 'William'")

    with pytest.warns(
        JupySQLQuotedNamedParametersWarning,
        match=expected_warning,
    ):
        ip.run_cell(cell)


def test_can_run_cte_that_references_a_table_whose_name_is_the_same_as_a_snippet(ip):
    # randomize the name to avoid collisions
    identifier = "shakespeare_" + str(uuid.uuid4())[:8]

    # create table
    ip.run_cell(
        f"""%%sql
create table {identifier} as select * from author where last_name = 'Shakespeare'
"""
    )

    # store a snippet with the same name
    ip.run_cell(
        f"""%%sql --save {identifier}
select * from author where last_name = 'some other last name'
"""
    )

    # this should query the table, not the snippet
    results = ip.run_cell(
        f"""%%sql
with author_subset as (
    select * from {identifier}
)
select * from author_subset
"""
    ).result

    assert results.dict() == {
        "first_name": ("William",),
        "last_name": ("Shakespeare",),
        "year_of_death": (1616,),
    }


def test_error_when_running_a_cte_and_passing_with_argument(ip):
    # randomize the name to avoid collisions
    identifier = "shakespeare_" + str(uuid.uuid4())[:8]

    # create table
    ip.run_cell(
        f"""%%sql
create table {identifier} as select * from author where last_name = 'Shakespeare'
"""
    )

    # store a snippet with the same name
    ip.run_cell(
        f"""%%sql --save {identifier}
select * from author where last_name = 'some other last name'
"""
    )

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell(
            f"""%%sql --with {identifier}
with author_subset as (
    select * from {identifier}
)
select * from author_subset
"""
        )

    assert "Cannot use --with with CTEs, remove --with and re-run the cell" in str(
        excinfo.value
    )


def test_error_if_using_persist_with_dbapi_connection(ip_dbapi):
    df = pd.DataFrame({"a": [1, 2, 3]})
    ip_dbapi.push({"df": df})

    with pytest.raises(UsageError) as excinfo:
        ip_dbapi.run_cell("%sql --persist df")

    message = (
        "--persist/--persist-replace is not available for "
        "DBAPI connections (only available for SQLAlchemy connections)"
    )
    assert message in str(excinfo.value)


@pytest.mark.parametrize("cell", ["%sql --persist df", "%sql --persist-replace df"])
def test_persist_uses_error_handling_method(ip, monkeypatch, cell):
    df = pd.DataFrame({"a": [1, 2, 3]})
    ip.push({"df": df})

    conn = ConnectionManager.current
    execute_with_error_handling_mock = Mock(wraps=conn._execute_with_error_handling)
    monkeypatch.setattr(
        conn, "_execute_with_error_handling", execute_with_error_handling_mock
    )

    ip.run_cell(cell)

    # ensure this got called because this function handles several sqlalchemy edge
    # cases
    execute_with_error_handling_mock.assert_called_once()


def test_error_when_using_section_argument_but_dsn_is_missing(ip_empty, tmp_empty):
    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'path/to/connections.ini'")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql --section some_section")

    assert excinfo.value.error_type == "FileNotFoundError"
    assert "%config SqlMagic.dsn_filename" in str(excinfo.value)
    assert "not found" in str(excinfo.value)


def test_error_when_using_section_argument_but_dsn_section_is_missing(
    ip_empty, tmp_empty
):
    Path("connections.ini").write_text(
        """
[section]
key = value
"""
    )

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql --section another_section")

    assert excinfo.value.error_type == "KeyError"

    message = (
        "The section 'another_section' does not exist in the "
        "connections file 'connections.ini'"
    )
    assert message in str(excinfo.value)


def test_error_when_using_section_argument_but_keys_are_invalid(ip_empty, tmp_empty):
    Path("connections.ini").write_text(
        """
[section]
key = value
"""
    )

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql --section section")

    assert excinfo.value.error_type == "TypeError"

    message = "%config SqlMagic.dsn_filename ('connections.ini') is invalid"
    assert message in str(excinfo.value)


def test_error_when_using_section_argument_but_values_are_invalid(ip_empty, tmp_empty):
    Path("connections.ini").write_text(
        """
[section]
drivername = not-a-driver
"""
    )

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql --section section")

    message = "Could not parse SQLAlchemy URL from string 'not-a-driver://'"
    assert message in str(excinfo.value)


def test_error_when_using_section_argument_and_alias(ip_empty, tmp_empty):
    Path("connections.ini").write_text(
        """
[duck]
drivername = duckdb
"""
    )

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql --section duck --alias stuff")

    assert excinfo.value.error_type == "UsageError"

    message = "Cannot use --section with --alias"
    assert message in str(excinfo.value)


def test_connect_to_db_in_connections_file_using_section_argument(ip_empty, tmp_empty):
    Path("connections.ini").write_text(
        """
[duck]
drivername = duckdb
"""
    )

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    ip_empty.run_cell("%sql --section duck")

    conns = ConnectionManager.connections
    assert conns == {"duck": ANY}


def test_connect_to_db_in_connections_file_using_section_name_between_square_brackets(
    ip_empty, tmp_empty
):
    Path("connections.ini").write_text(
        """
[duck]
drivername = duckdb
"""
    )

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    with pytest.warns(FutureWarning) as record:
        ip_empty.run_cell("%sql [duck]")

    assert "Starting connections with: %sql [section_name] is deprecated" in str(
        record[0].message
    )
    assert len(record) == 1
    conns = ConnectionManager.connections
    assert conns == {"duckdb://": ANY}


@pytest.mark.parametrize(
    "content, error_type, error_detail",
    [
        (
            """
[duck]
drivername = duckdb

[duck]
drivername = duckdb
""",
            "DuplicateSectionError",
            "section 'duck' already exists",
        ),
        (
            """
[duck]
drivername = duckdb
drivername = duckdb
""",
            "DuplicateOptionError",
            "option 'drivername' in section 'duck' already exists",
        ),
    ],
    ids=[
        "duplicate-section",
        "duplicate-key",
    ],
)
def test_error_when_ini_file_is_corrupted(
    ip_empty, tmp_empty, content, error_type, error_detail
):
    Path("connections.ini").write_text(content)

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    with pytest.raises(UsageError) as excinfo:
        ip_empty.run_cell("%sql --section duck")

    assert "An error happened when loading your %config SqlMagic.dsn_filename" in str(
        excinfo.value
    )

    assert error_type in str(excinfo.value)
    assert error_detail in str(excinfo.value)


def test_spaces_in_variable_name(ip_empty):
    ip_empty.run_cell("%sql duckdb://")
    ip_empty.run_cell("%sql create table 'table with spaces' (n INT)")
    ip_empty.run_cell('%sql create table "table with spaces2" (n INT)')
    tables_result = ip_empty.run_cell("%sqlcmd tables").result
    assert "table with spaces" in str(tables_result)
    assert "table with spaces2" in str(tables_result)

    ip_empty.run_cell("%sql INSERT INTO 'table with spaces' VALUES (1)")
    ip_empty.run_cell('%sql INSERT INTO "table with spaces" VALUES (2)')
    ip_empty.run_cell(
        """%%sql
INSERT INTO 'table with spaces' VALUES (3)
"""
    )
    ip_empty.run_cell(
        """%%sql
INSERT INTO "table with spaces" VALUES (4)
"""
    )
    select_result_with_single_quote = ip_empty.run_cell(
        "%sql SELECT * FROM 'table with spaces'"
    ).result
    assert select_result_with_single_quote.dict() == {"n": (1, 2, 3, 4)}

    select_result_with_double_quote = ip_empty.run_cell(
        '%sql SELECT * FROM "table with spaces"'
    ).result
    assert select_result_with_double_quote.dict() == {"n": (1, 2, 3, 4)}


@pytest.mark.parametrize(
    "query",
    [
        (" SELECT * FROM test"),
        ("  SELECT * FROM test"),
        ("  SELECT  * FROM test"),
        (
            """
SELECT * FROM test"""
        ),
        (
            """

SELECT * FROM test"""
        ),
        (
            """
SELECT
 * FROM test"""
        ),
        (
            """

SELECT
 * FROM test"""
        ),
    ],
)
def test_whitespaces_linebreaks_near_first_token(ip, query):
    expected_result = (
        "+---+------+\n"
        "| n | name |\n"
        "+---+------+\n"
        "| 1 | foo  |\n"
        "| 2 | bar  |\n"
        "+---+------+"
    )

    ip.user_global_ns["query"] = query
    out = ip.run_cell("%sql {{query}}").result
    assert str(out) == expected_result

    out = ip.run_cell(
        """%%sql
{{query}}"""
    ).result
    assert str(out) == expected_result


def test_summarize_in_duckdb(ip_empty):
    expected_result = {
        "column_name": ("id", "x"),
        "column_type": ("INTEGER", "INTEGER"),
        "min": ("1", "-1"),
        "max": ("3", "2"),
        "approx_unique": (3, 3),
        "avg": ("2.0", "0.6666666666666666"),
        "std": ("1.0", "1.5275252316519468"),
        "q25": ("1", "0"),
        "q50": ("2", "1"),
        "q75": ("3", "2"),
        "count": (3, 3),
        "null_percentage": (Decimal("0.00"), Decimal("0.00")),
    }

    ip_empty.run_cell("%sql duckdb://")
    ip_empty.run_cell("%sql CREATE TABLE table1 (id INTEGER, x INTEGER)")
    ip_empty.run_cell(
        """%%sql
INSERT INTO table1 VALUES (1, -1), (2, 1), (3, 2)"""
    )
    out = ip_empty.run_cell("%sql SUMMARIZE table1").result
    assert out.dict() == expected_result

    out = ip_empty.run_cell(
        """%%sql
SUMMARIZE table1"""
    ).result
    assert out.dict() == expected_result


def test_accessing_previously_nonexisting_file(ip_empty, tmp_empty, capsys):
    ip_empty.run_cell("%sql duckdb://")
    with pytest.raises(UsageError):
        ip_empty.run_cell("%sql SELECT * FROM 'data.csv' LIMIT 3")

    Path("data.csv").write_text(
        "name,age\nDan,33\nBob,19\nSheri,\nVin,33\nMick,\nJay,33\nSky,33"
    )
    expected = (
        "+-------+------+\n"
        "|  name | age  |\n"
        "+-------+------+\n"
        "|  Dan  |  33  |\n"
        "|  Bob  |  19  |\n"
        "| Sheri | None |\n"
        "+-------+------+"
    )

    ip_empty.run_cell("%sql SELECT * FROM 'data.csv' LIMIT 3")
    out, _ = capsys.readouterr()
    assert expected in out


expected_summarize = {
    "column_name": ("memid",),
    "column_type": ("BIGINT",),
    "min": ("1",),
    "max": ("8",),
    "approx_unique": (5,),
    "avg": ("3.8",),
    "std": ("2.7748873851023217",),
    "q25": ("2",),
    "q50": ("3",),
    "q75": ("6",),
    "count": (5,),
    "null_percentage": (Decimal("0.00"),),
}
expected_select = {"memid": (1, 2, 3, 5, 8)}


@pytest.mark.parametrize(
    "cell, expected_output",
    [
        ("%sql /* x */ SUMMARIZE df", expected_summarize),
        ("%sql /*x*//*x*/ SUMMARIZE /*x*/ df", expected_summarize),
        (
            """%%sql
            /*x*/
            SUMMARIZE df
            """,
            expected_summarize,
        ),
        (
            """%%sql
            /*x*/
            /*x*/
            -- comment
            SUMMARIZE df
            /*x*/
            """,
            expected_summarize,
        ),
        (
            """%%sql
            /*x*/
            SELECT * FROM df
            """,
            expected_select,
        ),
        (
            """%%sql
            /*x*/
            FROM df SELECT *
            """,
            expected_select,
        ),
    ],
)
def test_comments_in_duckdb_select_summarize(ip_empty, cell, expected_output):
    ip_empty.run_cell("%sql duckdb://")
    df = pd.DataFrame(  # noqa: F841
        data=dict(
            memid=[1, 2, 3, 5, 8],
        ),
    )
    out = ip_empty.run_cell(cell).result
    assert out.dict() == expected_output


@pytest.mark.parametrize(
    "setup, save_snippet, query_with_error, error_msgs, error_type",
    [
        (
            """
            %sql duckdb://
            %sql CREATE TABLE penguins (id INTEGER)
            %sql INSERT INTO penguins VALUES (1)
            """,
            """
            %%sql --save mysnippet
            SELECT * FROM penguins
            """,
            "%sql select not_a_function(id) from mysnippet",
            [
                "Scalar Function with name not_a_function does not exist!",
            ],
            "RuntimeError",
        ),
        (
            """
            %sql duckdb://
            %sql CREATE TABLE penguins (id INTEGER)
            %sql INSERT INTO penguins VALUES (1)
            """,
            """
            %%sql --save mysnippet
            SELECT * FROM penguins
            """,
            "%sql select not_a_function(id) from mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
                "There is no table with name 'mysnip'",
                "Table with name mysnip does not exist!",
            ],
            "TableNotFoundError",
        ),
        (
            "%sql sqlite://",
            """
            %%sql --save mysnippet
            select * from test
            """,
            "%sql select not_a_function(name) from mysnippet",
            [
                "no such function: not_a_function",
            ],
            "RuntimeError",
        ),
        (
            "%sql sqlite://",
            """
            %%sql --save mysnippet
            select * from test
            """,
            "%sql select not_a_function(name) from mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
                "There is no table with name 'mysnip'",
                "no such table: mysnip",
            ],
            "TableNotFoundError",
        ),
    ],
    ids=[
        "no-typo-duckdb",
        "with-typo-duckdb",
        "no-typo-sqlite",
        "with-typo-sqlite",
    ],
)
def test_query_snippet_invalid_function_error_message(
    ip, setup, save_snippet, query_with_error, error_msgs, error_type
):
    # Set up snippet.
    ip.run_cell(setup)
    ip.run_cell(save_snippet)

    # Run query
    with pytest.raises(UsageError) as excinfo:
        ip.run_cell(query_with_error)

    # Save result and test error message
    result_error = excinfo.value.error_type
    result_msg = str(excinfo.value)

    assert error_type == result_error
    assert all(msg in result_msg for msg in error_msgs)


@pytest.mark.parametrize(
    "sql_snippet, sql_query, expected_result, raises",
    [
        (
            """%%sql --save language_lt1
select * from languages where rating < 1""",
            """%%sql
create table langs as (
    select * from language_lt1
)""",
            """Your query is using the following snippets: language_lt1. \
The query is not a SELECT type query and as snippets only work \
with SELECT queries, CTE generation is disabled""",
            True,
        ),
        (
            """%%sql --save language_lt2
select * from languages where rating < 2""",
            """%%sql
with langs as (
    select * from language_lt2
) select * from langs """,
            """Your query is using one or more of the following snippets: \
language_lt2. JupySQL does not support snippet expansion within CTEs yet, \
CTE generation is disabled""",
            True,
        ),
        (
            """%%sql --save language_lt3
select * from languages where rating < 3""",
            """%%sql
create table langs1 as (
    WITH language_lt3 as (
        select * from languages where rating < 3
    )
    select * from language_lt3
) """,
            """Your query is using the following snippets: language_lt3. \
The query is not a SELECT type query and as snippets only work \
with SELECT queries, CTE generation is disabled""",
            False,
        ),
    ],
)
def test_warn_when_using_snippets_in_non_select_command(
    ip_empty, capsys, sql_snippet, sql_query, expected_result, raises
):
    ip_empty.run_cell("%sql duckdb://")
    ip_empty.run_cell("%sql create table languages (name VARCHAR, rating INTEGER)")
    ip_empty.run_cell(
        """%%sql
INSERT INTO languages VALUES ('Python', 1), ('Java', 0), ('OCaml', 2)"""
    )

    ip_empty.run_cell(sql_snippet)

    if raises:
        with pytest.raises(UsageError) as _:
            ip_empty.run_cell(sql_query)
    else:
        ip_empty.run_cell(sql_query)

    out, _ = capsys.readouterr()
    assert expected_result in out


@pytest.mark.parametrize(
    "query, query_type",
    [
        (
            """
            CREATE TABLE penguins AS (
                WITH my_penguins AS (
                    SELECT * FROM penguins.csv
                )
                SELECT * FROM my_penguins
            )
            """,
            "CREATE",
        ),
        (
            """
            WITH my_penguins AS (
                SELECT * FROM penguins.csv
            )
            SELECT * FROM my_penguins
            """,
            "SELECT",
        ),
        (
            """
            WITH my_penguins AS (
                SELECT * FROM penguins.csv
            )
            * FROM my_penguins
            """,
            None,
        ),
    ],
)
def test_get_query_type(query, query_type):
    assert get_query_type(query) == query_type


@pytest.mark.parametrize(
    "query, expected",
    [
        (
            "%sql select '{\"a\": 1}'::json -> 'a';",
            1,
        ),
        (
            '%sql select \'[{"b": "c"}]\'::json -> 0;',
            {"b": "c"},
        ),
        (
            "%sql select '{\"a\": 1}'::json ->> 'a';",
            "1",
        ),
        (
            '%sql select \'[{"b": "c"}]\'::json ->> 0;',
            '{"b":"c"}',
        ),
        (
            """%%sql select '{\"a\": 1}'::json
            ->
            'a';""",
            1,
        ),
        (
            """%%sql select '[{\"b\": \"c\"}]'::json
                ->
            0;""",
            {"b": "c"},
        ),
        (
            """%%sql select '{\"a\": 1}'::json
              ->>
            'a';""",
            "1",
        ),
        (
            """%%sql
            select
            \'[{"b": "c"}]\'::json
            ->>
            0;""",
            '{"b":"c"}',
        ),
        (
            "%sql SELECT '{\"a\": 1}'::json -> 'a';",
            1,
        ),
        (
            "%sql SELect '{\"a\": 1}'::json -> 'a';",
            1,
        ),
        (
            "%sql SELECT json('{\"a\": 1}') -> 'a';",
            1,
        ),
    ],
    ids=[
        "single-key",
        "single-index",
        "double-key",
        "double-index",
        "single-key-multi-line",
        "single-index-multi-line-tab",
        "double-key-multi-line-space",
        "double-index-multi-line",
        "single-key-all-caps",
        "single-key-mixed-caps",
        "single-key-cast-parentheses",
    ],
)
def test_json_arrow_operators(ip, query, expected):
    ip.run_cell("%sql duckdb://")
    result = ip.run_cell(query).result
    result = list(result.dict().values())[0][0]
    assert result == expected


@pytest.mark.parametrize(
    "query_save, query_snippet, expected",
    [
        (
            """%%sql --save snippet
            select '{\"a\": 1}'::json -> 'a';""",
            "%sql select * from snippet",
            1,
        ),
        (
            """%sql --save snippet select '[{\"b\": \"c\"}]'::json ->> 0;""",
            "%sql select * from snippet",
            '{"b":"c"}',
        ),
        (
            """%%sql --save snippet
            select '[1, 2, 3]'::json
            -> 2
            as number""",
            "%sql select number from snippet",
            3,
        ),
    ],
    ids=["cell-magic-key", "line-magic-index", "cell-magic-multi-line-as-column"],
)
def test_json_arrow_operators_with_snippets(ip, query_save, query_snippet, expected):
    ip.run_cell("%sql duckdb://")
    ip.run_cell(query_save)
    result = ip.run_cell(query_snippet).result
    result = list(result.dict().values())[0][0]
    assert result == expected


@pytest.mark.parametrize(
    "query, expected",
    [
        (
            """%%sql
SELECT 1""",
            1,
        ),
        (
            """%%sql
SELECT 1 -- comment""",
            1,
        ),
        (
            """%%sql
SELECT 1
-- comment""",
            1,
        ),
        (
            """%%sql
SELECT 1; -- comment""",
            1,
        ),
        (
            """%%sql
SELECT 1;
-- comment""",
            1,
        ),
        (
            """%%sql
-- comment before
SELECT 1;""",
            1,
        ),
        (
            """%%sql
-- comment before
SELECT 1;
-- comment after""",
            1,
        ),
        (
            """%%sql
SELECT 1; -- comment
SELECT 2""",
            2,
        ),
        (
            """%%sql
SELECT 1; -- comment
SELECT 2;""",
            2,
        ),
        (
            """%%sql
SELECT 1;
-- comment
SELECT 2;""",
            2,
        ),
        (
            """%%sql
SELECT 1;
-- comment before
SELECT 2;
-- comment after""",
            2,
        ),
        (
            """%%sql
SELECT 1; -- comment before
SELECT 2;
-- comment after""",
            2,
        ),
    ],
)
def test_query_comment_after_semicolon(ip, query, expected):
    result = ip.run_cell(query).result
    assert list(result.dict().values())[-1][0] == expected


@pytest.mark.parametrize(
    "query, error_type, error_message",
    [
        (
            """%%sql
SELECT * FROM snip;
SELECT * from temp;""",
            "TableNotFoundError",
            """If using snippets, you may pass the --with argument explicitly.
For more details please refer: \
https://jupysql.ploomber.io/en/latest/compose.html#with-argument

There is no table with name 'snip'.
Did you mean: 'snippet'


Original error message from DB driver:
(duckdb.duckdb.CatalogException) Catalog Error: Table with name snip does not exist!
Did you mean "temp"?
LINE 1: SELECT * FROM snip;
                      ^
[SQL: SELECT * FROM snip;]""",
        ),
        (
            """%%sql
SELECT * FROM snippet;
SELECT * from tem;""",
            "RuntimeError",
            """If using snippets, you may pass the --with argument explicitly.
For more details please refer: \
https://jupysql.ploomber.io/en/latest/compose.html#with-argument


Original error message from DB driver:
(duckdb.duckdb.CatalogException) Catalog Error: Table with name tem does not exist!
Did you mean "temp"?
LINE 1: SELECT * from tem;
                      ^
[SQL: SELECT * from tem;]""",
        ),
        (
            """%%sql
SELECT * FROM snip;
SELECT * from tem;""",
            "TableNotFoundError",
            """If using snippets, you may pass the --with argument explicitly.
For more details please refer: \
https://jupysql.ploomber.io/en/latest/compose.html#with-argument

There is no table with name 'snip'.
Did you mean: 'snippet'


Original error message from DB driver:
(duckdb.duckdb.CatalogException) Catalog Error: Table with name snip does not exist!
Did you mean "temp"?
LINE 1: SELECT * FROM snip;
                      ^
[SQL: SELECT * FROM snip;]""",
        ),
        (
            """%%sql
SELECT * FROM s;
SELECT * from temp;""",
            "RuntimeError",
            """If using snippets, you may pass the --with argument explicitly.
For more details please refer: \
https://jupysql.ploomber.io/en/latest/compose.html#with-argument


Original error message from DB driver:
(duckdb.duckdb.CatalogException) Catalog Error: Table with name s does not exist!
Did you mean "temp"?
LINE 1: SELECT * FROM s;
                      ^
[SQL: SELECT * FROM s;]""",
        ),
        (
            """%%sql
DROP TABLE temp;
SELECT * FROM snippet;
SELECT * from temp;""",
            "RuntimeError",
            """If using snippets, you may pass the --with argument explicitly.
For more details please refer: \
https://jupysql.ploomber.io/en/latest/compose.html#with-argument


Original error message from DB driver:
(duckdb.duckdb.CatalogException) Catalog Error: Table with name snippet does not exist!
Did you mean "pg_type"?
LINE 1: SELECT * FROM snippet;
                      ^
[SQL: SELECT * FROM snippet;]""",
        ),
    ],
    ids=[
        "snippet-typo",
        "table-typo",
        "both-typo",
        "snippet-typo-no-suggestion",
        "no-typo-drop-table",
    ],
)
def test_table_does_not_exist_with_snippet_error(
    ip_empty, query, error_type, error_message
):
    ip_empty.run_cell(
        """%load_ext sql
%sql duckdb://"""
    )
    # Create temp table
    ip_empty.run_cell(
        """%%sql
CREATE TABLE temp AS
SELECT * FROM penguins.csv"""
    )

    # Create snippet
    ip_empty.run_cell(
        """%%sql --save snippet
SELECT * FROM penguins.csv;"""
    )

    # Run query
    with pytest.raises(Exception) as excinfo:
        ip_empty.run_cell(query)

    # Test error and message
    assert error_type == excinfo.value.error_type
    assert error_message in str(excinfo.value)


@pytest.mark.parametrize(
    "query, expected",
    [
        ("%sql select 5 * -2", (-10,)),
        ("%sql select 5 * - 2", (-10,)),
        ("%sql select 5 * -2;", (-10,)),
        ("%sql select -5 * 2;", (-10,)),
        ("%sql select 5 * -2 ;", (-10,)),
        ("%sql select 5 * - 2;", (-10,)),
        ("%sql select x * -2 from number_table", (-8, 10, -4, 0, 10, 4, 4, 8, -4, -8)),
        ("%sql select x *-2 from number_table", (-8, 10, -4, 0, 10, 4, 4, 8, -4, -8)),
        ("%sql select x * - 2 from number_table", (-8, 10, -4, 0, 10, 4, 4, 8, -4, -8)),
        ("%sql select x *- 2 from number_table", (-8, 10, -4, 0, 10, 4, 4, 8, -4, -8)),
        ("%sql select -x * 2 from number_table", (-8, 10, -4, 0, 10, 4, 4, 8, -4, -8)),
        ("%sql select - x * 2 from number_table", (-8, 10, -4, 0, 10, 4, 4, 8, -4, -8)),
        ("%sql select - x* 2 from number_table", (-8, 10, -4, 0, 10, 4, 4, 8, -4, -8)),
    ],
)
def test_negative_operations_query(ip, query, expected):
    result = ip.run_cell(query).result
    assert list(result.dict().values())[-1] == expected


def test_bracket_var_substitution_save(ip):
    ip.user_global_ns["col"] = "first_name"
    ip.user_global_ns["snippet"] = "mysnippet"
    ip.run_cell(
        "%sql --save {{snippet}} SELECT * FROM author WHERE {{col}} = 'William' "
    )
    out = ip.run_cell("%sql SELECT * FROM {{snippet}}").result
    assert out[0] == (
        "William",
        "Shakespeare",
        1616,
    )


def test_var_substitution_save_with(ip):
    ip.user_global_ns["col"] = "first_name"
    ip.user_global_ns["snippet_one"] = "william"
    ip.user_global_ns["snippet_two"] = "bertold"
    ip.run_cell(
        "%sql --save {{snippet_one}} SELECT * FROM author WHERE {{col}} = 'William' "
    )
    ip.run_cell(
        "%sql --save {{snippet_two}} SELECT * FROM author WHERE {{col}} = 'Bertold' "
    )
    out = ip.run_cell(
        """%%sql --with {{snippet_one}} --with {{snippet_two}}
SELECT * FROM {{snippet_one}}
UNION
SELECT * FROM {{snippet_two}}
"""
    ).result

    assert out[1] == (
        "William",
        "Shakespeare",
        1616,
    )
    assert out[0] == (
        "Bertold",
        "Brecht",
        1956,
    )


def test_var_substitution_alias(clean_conns, ip_empty, tmp_empty):
    ip_empty.user_global_ns["alias"] = "one"
    ip_empty.run_cell("%sql sqlite:///one.db --alias {{alias}}")
    assert {"one"} == set(ConnectionManager.connections)


@pytest.mark.parametrize(
    "close_cell",
    [
        "%sql -x {{alias}}",
        "%sql --close {{alias}}",
    ],
)
def test_var_substitution_close_connection_with_alias(ip, tmp_empty, close_cell):
    ip.user_global_ns["alias"] = "one"
    process = psutil.Process()

    ip.run_cell("%sql sqlite:///one.db --alias {{alias}}")

    assert {Path(f.path).name for f in process.open_files()} >= {"one.db"}

    ip.run_cell(close_cell)

    assert "sqlite:///one.db" not in ConnectionManager.connections
    assert "first" not in ConnectionManager.connections
    assert "one.db" not in {Path(f.path).name for f in process.open_files()}


def test_var_substitution_section(ip_empty, tmp_empty):
    Path("connections.ini").write_text(
        """
[duck]
drivername = duckdb
"""
    )
    ip_empty.user_global_ns["section"] = "duck"

    ip_empty.run_cell("%config SqlMagic.dsn_filename = 'connections.ini'")

    ip_empty.run_cell("%sql --section {{section}}")

    conns = ConnectionManager.connections
    assert conns == {"duck": ANY}


@pytest.mark.parametrize(
    "query, expected",
    [
        (
            '%sql select json(\'[{"a":1}, {"b":2}]\')',
            "[{'a': 1}, {'b': 2}]",
        ),
        (
            '%sql select \'[{"a":1}, {"b":2}]\'::json',
            "[{'a': 1}, {'b': 2}]",
        ),
    ],
)
def test_disable_named_parameters_with_json(ip, query, expected):
    ip.run_cell("%sql duckdb://")
    ip.run_cell("%config SqlMagic.named_parameters='disabled'")
    result = ip.run_cell(query).result
    assert str(list(result.dict().values())[0][0]) == expected


def test_disabled_named_parameters_shows_disabled_warning(ip):
    ip.run_cell("%config SqlMagic.named_parameters='disabled'")
    query_should_warn = "%sql select json('[{\"a\"::1}')"

    with pytest.raises(UsageError) as excinfo:
        ip.run_cell(query_should_warn)

    expected_warning = (
        'The named parameters feature is "disabled". '
        'Enable it with: %config SqlMagic.named_parameters="enabled".\n'
        "For more info, see the docs: "
        "https://jupysql.ploomber.io/en/latest/api/configuration.html"
    )

    assert expected_warning in str(excinfo.value)
