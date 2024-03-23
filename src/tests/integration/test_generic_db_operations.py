from uuid import uuid4
import shutil
from matplotlib import pyplot as plt
import pytest
import warnings
from sql.error_handler import CTE_MSG
from unittest.mock import ANY, Mock
from IPython.core.error import UsageError

import math

ALL_DATABASES = [
    "ip_with_postgreSQL",
    "ip_with_mySQL",
    "ip_with_mariaDB",
    "ip_with_SQLite",
    "ip_with_duckDB_native",
    "ip_with_duckDB",
    "ip_with_MSSQL",
    "ip_with_Snowflake",
    "ip_with_oracle",
    "ip_with_clickhouse",
    "ip_with_spark",
]

# NOTE: We don't need to add tests for Snowflake and Redshift
# for future PRs.
# Reference issue: https://github.com/ploomber/jupysql/issues/984


@pytest.fixture(autouse=True)
def run_around_tests(tmpdir_factory):
    # Create tmp folder
    my_tmpdir = tmpdir_factory.mktemp("tmp")
    yield my_tmpdir
    # Destroy tmp folder
    shutil.rmtree(str(my_tmpdir))


@pytest.mark.parametrize(
    "ip_with_dynamic_db, query_prefix, query_suffix",
    [
        ("ip_with_postgreSQL", "", "LIMIT 3"),
        ("ip_with_mySQL", "", "LIMIT 3"),
        ("ip_with_mariaDB", "", "LIMIT 3"),
        ("ip_with_SQLite", "", "LIMIT 3"),
        ("ip_with_duckDB_native", "", "LIMIT 3"),
        ("ip_with_duckDB", "", "LIMIT 3"),
        ("ip_with_Snowflake", "", "LIMIT 3"),
        ("ip_with_redshift", "", "LIMIT 3"),
        ("ip_with_clickhouse", "", "LIMIT 3"),
        ("ip_with_oracle", "", "FETCH FIRST 3 ROWS ONLY"),
        ("ip_with_MSSQL", "TOP 3", ""),
        ("ip_with_spark", "", "LIMIT 3"),
    ],
)
def test_run_query(
    ip_with_dynamic_db, query_prefix, query_suffix, request, test_table_name_dict
):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    # run a query
    out = ip_with_dynamic_db.run_cell(
        f"%sql SELECT {query_prefix} * FROM {test_table_name_dict['taxi']} \
            {query_suffix}"
    )

    # test --save
    ip_with_dynamic_db.run_cell(
        f"%sql --save taxi_subset --no-execute SELECT {query_prefix} * FROM\
          {test_table_name_dict['taxi']} {query_suffix}"
    )

    out_query_with_save_arg = ip_with_dynamic_db.run_cell(
        "%sql --with taxi_subset SELECT * FROM taxi_subset"
    )

    assert len(out.result) == 3
    assert len(out_query_with_save_arg.result) == 3


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_mySQL",
        "ip_with_mariaDB",
        "ip_with_SQLite",
        "ip_with_duckDB",
        "ip_with_duckDB_native",
        "ip_with_Snowflake",
        "ip_with_redshift",
        "ip_with_clickhouse",
        "ip_with_spark",
    ],
)
def test_handle_multiple_open_result_sets(
    ip_with_dynamic_db, request, test_table_name_dict
):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    taxi_table = test_table_name_dict["taxi"]
    numbers_table = test_table_name_dict["numbers"]

    ip_with_dynamic_db.run_cell("%config SqlMagic.displaylimit = 2")

    taxi = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM {taxi_table} LIMIT 5"
    ).result

    numbers = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM {numbers_table} LIMIT 5"
    ).result

    # NOTE: we do not check the value of the indexes because snowflake does not support
    # them
    assert taxi.dict()["taxi_driver_name"] == (
        "Eric Ken",
        "John Smith",
        "Kevin Kelly",
        "Eric Ken",
        "John Smith",
    )
    assert numbers.dict()["numbers_elements"] == (1, 2, 3, 1, 2)


@pytest.mark.parametrize(
    "ip_with_dynamic_db, args",
    [
        ("ip_with_postgreSQL", ""),
        ("ip_with_mySQL", ""),
        ("ip_with_mariaDB", ""),
        ("ip_with_SQLite", ""),
        ("ip_with_duckDB", ""),
        pytest.param(
            "ip_with_duckDB_native",
            "",
            marks=pytest.mark.xfail(
                reason="'duckdb.DuckDBPyConnection' object has no attribute 'rowcount'"
            ),
        ),
        # snowflake and redshift do not support "CREATE INDEX", so we need to
        # pass --no-index
        ("ip_with_Snowflake", "--no-index"),
        ("ip_with_redshift", "--no-index"),
        pytest.param(
            "ip_with_clickhouse",
            "",
            marks=pytest.mark.xfail(
                reason="sqlalchemy.exc.CompileError: "
                "No engine for table <table_name>"
            ),
        ),
        ("ip_with_spark", "--no-index"),
    ],
)
def test_create_table_with_indexed_df(
    ip_with_dynamic_db, args, request, test_table_name_dict
):
    limit = 15
    expected = 15

    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    # Clean up

    ip_with_dynamic_db.run_cell("%config SqlMagic.displaylimit = 0")

    ip_with_dynamic_db.run_cell(
        f"%sql DROP TABLE IF EXISTS {test_table_name_dict['new_table_from_df']}"
    )

    # Prepare DF
    ip_with_dynamic_db.run_cell(
        f"results = %sql SELECT * FROM {test_table_name_dict['taxi']}\
          LIMIT {limit}"
    )

    # Prepare expected df
    expected_df = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM {test_table_name_dict['taxi']}\
          LIMIT {limit}"
    )

    ip_with_dynamic_db.run_cell(
        f"{test_table_name_dict['new_table_from_df']} = results.DataFrame()"
    )
    # Create table from DF
    persist_out = ip_with_dynamic_db.run_cell(
        f"%sql --persist {test_table_name_dict['new_table_from_df']} {args}"
    )
    out_df = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM {test_table_name_dict['new_table_from_df']}"
    )
    assert persist_out.error_in_exec is None and out_df.error_in_exec is None
    assert len(out_df.result) == expected

    expected_df_ = expected_df.result.DataFrame()
    out_df_ = out_df.result.DataFrame()

    assert expected_df_.equals(out_df_.loc[:, out_df_.columns != "level_0"])


def get_connection_count(ip_with_dynamic_db):
    out = ip_with_dynamic_db.run_line_magic("sql", "-l")
    print("Current connections:", out)
    connections_count = len(out)
    return connections_count


@pytest.mark.parametrize(
    "ip_with_dynamic_db, expected",
    [
        ("ip_with_postgreSQL", 1),
        ("ip_with_mySQL", 1),
        ("ip_with_mariaDB", 1),
        ("ip_with_SQLite", 1),
        ("ip_with_duckDB", 1),
        ("ip_with_duckDB_native", 1),
        ("ip_with_MSSQL", 1),
        ("ip_with_Snowflake", 1),
        ("ip_with_clickhouse", 1),
        ("ip_with_spark", 1),
    ],
)
def test_active_connection_number(ip_with_dynamic_db, expected, request):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    assert get_connection_count(ip_with_dynamic_db) == expected


@pytest.mark.parametrize(
    "ip_with_dynamic_db, config_key",
    [
        ("ip_with_postgreSQL", "postgreSQL"),
        ("ip_with_mySQL", "mySQL"),
        ("ip_with_mariaDB", "mariaDB"),
        ("ip_with_SQLite", "SQLite"),
        ("ip_with_duckDB", "duckDB"),
        ("ip_with_duckDB_native", "duckDB"),
        ("ip_with_MSSQL", "MSSQL"),
        ("ip_with_Snowflake", "Snowflake"),
        ("ip_with_oracle", "oracle"),
        ("ip_with_clickhouse", "clickhouse"),
    ],
)
def test_close_and_connect(
    ip_with_dynamic_db, config_key, request, get_database_config_helper
):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    conn_alias = get_database_config_helper.get_database_config(config_key)["alias"]
    database_url = get_database_config_helper.get_database_url(config_key)
    # Disconnect

    ip_with_dynamic_db.run_cell("%sql -x " + conn_alias)

    assert get_connection_count(ip_with_dynamic_db) == 0
    # Connect, also check there is no error on re-connecting
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        ip_with_dynamic_db.run_cell("%sql " + database_url + " --alias " + conn_alias)

    assert get_connection_count(ip_with_dynamic_db) == 1


@pytest.mark.parametrize(
    "cell",
    [
        (
            "%sqlplot histogram --with plot_something_subset --table\
              plot_something_subset --column x"
        ),
        (
            "%sqlplot hist --with plot_something_subset --table\
              plot_something_subset --column x"
        ),
        (
            "%sqlplot histogram --with plot_something_subset --table\
              plot_something_subset --column x --bins 10"
        ),
        (
            "%sqlplot histogram --with plot_something_subset --table\
              plot_something_subset --column x --breaks 0 2 3 4 5"
        ),
        (
            "%sqlplot histogram --with plot_something_subset --table\
              plot_something_subset --column x --binwidth 1"
        ),
    ],
    ids=[
        "histogram",
        "hist",
        "histogram-bins",
        "histogram-breaks",
        "histogram-binwidth",
    ],
)
@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        ("ip_with_postgreSQL"),
        ("ip_with_mySQL"),
        ("ip_with_mariaDB"),
        ("ip_with_SQLite"),
        ("ip_with_duckDB"),
        ("ip_with_Snowflake"),
        ("ip_with_duckDB_native"),
        ("ip_with_redshift"),
        ("ip_with_spark"),
        pytest.param(
            "ip_with_MSSQL",
            marks=pytest.mark.xfail(reason="sqlglot does not support SQL server"),
        ),
        pytest.param(
            "ip_with_clickhouse",
            marks=pytest.mark.xfail(
                reason="Plotting from snippet not working in clickhouse"
            ),
        ),
    ],
)
def test_sqlplot_histogram(ip_with_dynamic_db, cell, request, test_table_name_dict):
    # clean current Axes
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    plt.cla()

    ip_with_dynamic_db.run_cell(
        f"%sql --save plot_something_subset\
         --no-execute SELECT * from {test_table_name_dict['plot_something']} LIMIT 3"
    )
    out = ip_with_dynamic_db.run_cell(cell)

    assert type(out.result).__name__ in {"Axes", "AxesSubplot"}


BOX_PLOT_FAIL_REASON = (
    "Known issue, the SQL engine must support percentile_disc() SQL clause"
)


@pytest.mark.parametrize(
    "cell",
    [
        "%sqlplot boxplot --with plot_something_subset \
        --table plot_something_subset --column x",
        "%sqlplot box --with plot_something_subset \
        --table plot_something_subset --column x",
        "%sqlplot boxplot --with plot_something_subset \
        --table plot_something_subset --column x --orient h",
        "%sqlplot boxplot --with plot_something_subset \
        --table plot_something_subset --column x",
    ],
    ids=[
        "boxplot",
        "box",
        "boxplot-with-horizontal",
        "boxplot-with",
    ],
)
@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_duckDB",
        "ip_with_redshift",
        "ip_with_MSSQL",
        pytest.param(
            "ip_with_duckDB_native",
            marks=pytest.mark.xfail(reason="Custom driver not supported"),
        ),
        pytest.param(
            "ip_with_mySQL", marks=pytest.mark.xfail(reason=BOX_PLOT_FAIL_REASON)
        ),
        pytest.param(
            "ip_with_mariaDB", marks=pytest.mark.xfail(reason=BOX_PLOT_FAIL_REASON)
        ),
        pytest.param(
            "ip_with_SQLite", marks=pytest.mark.xfail(reason=BOX_PLOT_FAIL_REASON)
        ),
        pytest.param(
            "ip_with_Snowflake",
            marks=pytest.mark.xfail(
                reason="Something wrong with sqlplot boxplot in snowflake"
            ),
        ),
        pytest.param(
            "ip_with_clickhouse",
            marks=pytest.mark.xfail(
                reason="Plotting from snippet not working in clickhouse"
            ),
        ),
        pytest.param(
            "ip_with_spark", marks=pytest.mark.xfail(reason=BOX_PLOT_FAIL_REASON)
        ),
    ],
)
def test_sqlplot_boxplot(ip_with_dynamic_db, cell, request, test_table_name_dict):
    # clean current Axes
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    plt.cla()
    ip_with_dynamic_db.run_cell(
        f"%sql --save plot_something_subset --no-execute\
          SELECT * from {test_table_name_dict['plot_something']} LIMIT 3"
    )

    out = ip_with_dynamic_db.run_cell(cell)

    assert type(out.result).__name__ in {"Axes", "AxesSubplot"}


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_duckDB",
        "ip_with_redshift",
        "ip_with_MSSQL",
        "ip_with_spark",
    ],
)
def test_sqlplot_bar(ip_with_dynamic_db, request, test_table_name_dict):
    plt.cla()

    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    ip_with_dynamic_db.run_cell(
        f"%sql --save plot_something_subset --no-execute\
          SELECT * from {test_table_name_dict['plot_something']} LIMIT 3"
    )

    cell = (
        "%sqlplot bar --with plot_something_subset "
        "--table plot_something_subset --column x"
    )
    out = ip_with_dynamic_db.run_cell(cell)

    assert type(out.result).__name__ in {"Axes", "AxesSubplot"}


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_duckDB",
        "ip_with_redshift",
        "ip_with_MSSQL",
        "ip_with_spark",
    ],
)
def test_sqlplot_pie(ip_with_dynamic_db, request, test_table_name_dict):
    plt.cla()

    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    ip_with_dynamic_db.run_cell(
        f"%sql --save plot_something_subset --no-execute\
          SELECT * from {test_table_name_dict['plot_something']} LIMIT 3"
    )

    cell = (
        "%sqlplot pie --with plot_something_subset "
        "--table plot_something_subset --column x"
    )
    out = ip_with_dynamic_db.run_cell(cell)

    assert type(out.result).__name__ in {"Axes", "AxesSubplot"}


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        ("ip_with_postgreSQL"),
        ("ip_with_duckDB"),
        ("ip_with_Snowflake"),
        ("ip_with_duckDB_native"),
        pytest.param(
            "ip_with_redshift",
            marks=pytest.mark.xfail(reason="permission denied for database dev"),
        ),
        pytest.param(
            "ip_with_SQLite",
            marks=pytest.mark.xfail(reason="does not support schema"),
        ),
        pytest.param(
            "ip_with_mariaDB",
            marks=pytest.mark.xfail(reason="schema access denied"),
        ),
        pytest.param(
            "ip_with_mySQL",
            marks=pytest.mark.xfail(reason="schema access denied"),
        ),
        pytest.param(
            "ip_with_MSSQL",
            marks=pytest.mark.xfail(reason="sqlplot does not support SQL server"),
        ),
        pytest.param(
            "ip_with_clickhouse",
            marks=pytest.mark.xfail(
                reason="Plotting from snippet not working in clickhouse"
            ),
        ),
        "ip_with_spark",
    ],
)
def test_sqlplot_using_schema(ip_with_dynamic_db, request):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    plt.cla()
    ip_with_dynamic_db.run_cell(
        """%%sql
CREATE SCHEMA IF NOT EXISTS schema1;
CREATE TABLE IF NOT EXISTS schema1.table1 (
    x INTEGER,
    y INTEGER
);

INSERT INTO schema1.table1 (x, y)
VALUES
    (1, 2),
    (3, 4),
    (5, 6);
"""
    )

    out = ip_with_dynamic_db.run_cell(
        "%sqlplot histogram --table schema1.table1 --column x"
    )
    assert type(out.result).__name__ in {"Axes", "AxesSubplot"}

    out = ip_with_dynamic_db.run_cell(
        "%sqlplot histogram --table table1 --schema schema1 --column x"
    )
    assert type(out.result).__name__ in {"Axes", "AxesSubplot"}


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        ("ip_with_postgreSQL"),
        ("ip_with_mySQL"),
        ("ip_with_mariaDB"),
        ("ip_with_SQLite"),
        ("ip_with_duckDB"),
        ("ip_with_redshift"),
        pytest.param(
            "ip_with_duckDB_native",
            marks=pytest.mark.xfail(reason="not supported yet for native connections"),
        ),
        pytest.param(
            "ip_with_MSSQL",
            marks=pytest.mark.xfail(reason="not working yet"),
        ),
        ("ip_with_Snowflake"),
        ("ip_with_oracle"),
        ("ip_with_clickhouse"),
        ("ip_with_spark"),
    ],
)
def test_sqlcmd_test(ip_with_dynamic_db, request, test_table_name_dict):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    table = test_table_name_dict["numbers"]

    ip_with_dynamic_db.run_cell(f"%sql select * from {table}")

    with pytest.raises(UsageError) as excinfo:
        ip_with_dynamic_db.run_cell(
            f"%sqlcmd test --table {table} --column numbers_elements "
            "--less-than 1 --greater 2"
        )

    assert "The above values do not match your test requirements." in str(excinfo.value)


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        ("ip_with_postgreSQL"),
        ("ip_with_mySQL"),
        ("ip_with_mariaDB"),
        ("ip_with_SQLite"),
        ("ip_with_duckDB"),
        ("ip_with_duckDB_native"),
        ("ip_with_MSSQL"),
        pytest.param(
            "ip_with_Snowflake",
            marks=pytest.mark.xfail(
                reason="Something wrong with test_sql_cmd_magic_dos in snowflake"
            ),
        ),
        ("ip_with_oracle"),
        ("ip_with_clickhouse"),
        ("ip_with_spark"),
    ],
)
def test_profile_data_mismatch(ip_with_dynamic_db, request, capsys):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    ip_with_dynamic_db.run_cell(
        """
        %%sql sqlite://
        CREATE TABLE people (name varchar(50),age varchar(50),number int,
            country varchar(50),gender_1 varchar(50), gender_2 varchar(50));
        INSERT INTO people VALUES ('joe', '48', 82, 'usa', '0', 'male');
        INSERT INTO people VALUES ('paula', '50', 93, 'uk', '1', 'female');
        """
    )

    out = ip_with_dynamic_db.run_cell("%sqlcmd profile -t people").result

    stats_table_html = out._table_html

    assert "td:nth-child(3)" in stats_table_html
    assert "td:nth-child(6)" in stats_table_html
    assert "td:nth-child(7)" not in stats_table_html
    assert "td:nth-child(4)" not in stats_table_html
    assert (
        "Columns <code>age</code><code>gender_1</code> have a datatype mismatch"
        in stats_table_html
    )


@pytest.mark.parametrize(
    "ip_with_dynamic_db, table, table_columns, expected, message",
    [
        (
            "ip_with_postgreSQL",
            "taxi",
            ["index", "taxi_driver_name"],
            {
                "count": [45, 45],
                "mean": [22.0, math.nan],
                "min": [0, "Eric Ken"],
                "max": [44, "Kevin Kelly"],
                "unique": [45, 3],
                "freq": [1, 15],
                "top": [0, "Eric Ken"],
                "std": ["1.299e+01", math.nan],
                "25%": [11.0, math.nan],
                "50%": [22.0, math.nan],
                "75%": [33.0, math.nan],
            },
            None,
        ),
        pytest.param(
            "ip_with_mySQL",
            "taxi",
            ["taxi_driver_name"],
            {
                "count": [45],
                "mean": [0.0],
                "min": ["Eric Ken"],
                "max": ["Kevin Kelly"],
                "unique": [3],
                "freq": [15],
                "top": ["Kevin Kelly"],
            },
            "Following statistics are not available in",
            marks=pytest.mark.xfail(
                reason="Need to get column names from table with a different query"
            ),
        ),
        pytest.param(
            "ip_with_mariaDB",
            "taxi",
            ["taxi_driver_name"],
            {
                "count": [45],
                "mean": [0.0],
                "min": ["Eric Ken"],
                "max": ["Kevin Kelly"],
                "unique": [3],
                "freq": [15],
                "top": ["Kevin Kelly"],
            },
            "Following statistics are not available in",
            marks=pytest.mark.xfail(
                reason="Need to get column names from table with a different query"
            ),
        ),
        (
            "ip_with_SQLite",
            "taxi",
            ["taxi_driver_name"],
            {
                "count": [45],
                "mean": [0.0],
                "min": ["Eric Ken"],
                "max": ["Kevin Kelly"],
                "unique": [3],
                "freq": [15],
                "top": ["Kevin Kelly"],
            },
            "Following statistics are not available in",
        ),
        (
            "ip_with_duckDB",
            "taxi",
            ["index", "taxi_driver_name"],
            {
                "count": [45, 45],
                "mean": [22.0, math.nan],
                "min": [0, "Eric Ken"],
                "max": [44, "Kevin Kelly"],
                "unique": [45, 3],
                "freq": [1, 15],
                "top": [0, "Eric Ken"],
                "std": ["1.299e+01", math.nan],
                "25%": [11.0, math.nan],
                "50%": [22.0, math.nan],
                "75%": [33.0, math.nan],
            },
            None,
        ),
        (
            "ip_with_duckDB_native",
            "taxi",
            ["index", "taxi_driver_name"],
            {
                "count": [45, 45],
                "mean": [22.0, math.nan],
                "min": [0, "Eric Ken"],
                "max": [44, "Kevin Kelly"],
                "unique": [45, 3],
                "freq": [1, 15],
                "top": [0, "Eric Ken"],
                "std": ["1.299e+01", math.nan],
                "25%": [11.0, math.nan],
                "50%": [22.0, math.nan],
                "75%": [33.0, math.nan],
            },
            None,
        ),
        (
            "ip_with_MSSQL",
            "taxi",
            ["taxi_driver_name"],
            {"unique": [3], "min": ["Eric Ken"], "max": ["Kevin Kelly"], "count": [45]},
            "Following statistics are not available in",
        ),
        pytest.param(
            "ip_with_Snowflake",
            "taxi",
            ["taxi_driver_name"],
            {},
            None,
            marks=pytest.mark.xfail(
                reason="Something wrong with test_profile_query in snowflake"
            ),
        ),
        pytest.param(
            "ip_with_oracle",
            "taxi",
            ["taxi_driver_name"],
            {},
            None,
            marks=pytest.mark.xfail(
                reason="Something wrong with test_profile_query in snowflake"
            ),
        ),
        pytest.param(
            "ip_with_clickhouse",
            "taxi",
            ["taxi_driver_name"],
            {
                "count": [45],
                "mean": [0.0],
                "min": ["Eric Ken"],
                "max": ["Kevin Kelly"],
                "unique": [3],
                "freq": [15],
                "top": ["Kevin Kelly"],
            },
            "Following statistics are not available in",
        ),
        (
            "ip_with_spark",
            "taxi",
            ["taxi_driver_name"],
            {
                "count": [45],
                "mean": [math.nan],
                "min": ["Eric Ken"],
                "max": ["Kevin Kelly"],
                "unique": [3],
                "freq": [15],
                "top": ["Eric Ken"],
                "std": [math.nan],
                "25%": [math.nan],
                "50%": [math.nan],
                "75%": [math.nan],
            },
            None,
        ),
    ],
)
def test_sqlcmd_profile(
    request,
    ip_with_dynamic_db,
    table,
    table_columns,
    expected,
    test_table_name_dict,
    message,
):
    pytest.skip("Skip on unclosed session issue")
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    out = ip_with_dynamic_db.run_cell(
        f"""
        %sqlcmd profile --table "{test_table_name_dict[table]}"
        """
    ).result

    stats_table = out._table
    stats_table_html = out._table_html
    assert len(stats_table.rows) == len(expected)

    for row in stats_table:
        criteria = row.get_string(fields=[" "], border=False).strip()

        for i, column in enumerate(table_columns):
            cell_value = row.get_string(
                fields=[column], border=False, header=False
            ).strip()

            assert criteria in expected
            assert cell_value == str(expected[criteria][i])

    if message:
        assert message in stats_table_html


@pytest.mark.parametrize(
    "table",
    [
        "numbers",
    ],
)
@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        ("ip_with_postgreSQL"),
        ("ip_with_mySQL"),
        ("ip_with_mariaDB"),
        ("ip_with_SQLite"),
        ("ip_with_duckDB"),
        ("ip_with_redshift"),
        pytest.param(
            "ip_with_duckDB_native",
            marks=pytest.mark.xfail(reason="Bug #428"),
        ),
        ("ip_with_MSSQL"),
        ("ip_with_Snowflake"),
        ("ip_with_clickhouse"),
        pytest.param(
            "ip_with_spark",
            marks=pytest.mark.xfail(reason="Not Implemented"),
        ),
    ],
)
def test_sqlcmd_columns(ip_with_dynamic_db, table, request, test_table_name_dict):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    out = ip_with_dynamic_db.run_cell(
        f"%sqlcmd columns --table {test_table_name_dict[table]}"
    )
    assert out.result


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        ("ip_with_postgreSQL"),
        ("ip_with_mySQL"),
        ("ip_with_mariaDB"),
        ("ip_with_SQLite"),
        ("ip_with_duckDB"),
        ("ip_with_redshift"),
        pytest.param(
            "ip_with_duckDB_native",
            marks=pytest.mark.xfail(reason="Bug #428"),
        ),
        ("ip_with_MSSQL"),
        ("ip_with_Snowflake"),
        ("ip_with_clickhouse"),
        pytest.param(
            "ip_with_spark",
            marks=pytest.mark.xfail(reason="Not Implemented"),
        ),
    ],
)
def test_sqlcmd_tables(ip_with_dynamic_db, request):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    out = ip_with_dynamic_db.run_cell("%sqlcmd tables")
    assert out.result


@pytest.mark.parametrize(
    "cell",
    [
        "%%sql\nSELECT * FROM numbers WHERE 0=1",
        "%%sql\nSELECT *\n-- %one $another\nFROM numbers WHERE 0=1",
    ],
    ids=[
        "simple-query",
        "interpolation-like-comment",
    ],
)
@pytest.mark.parametrize("ip_with_dynamic_db", ALL_DATABASES)
def test_sql_query(ip_with_dynamic_db, cell, request, test_table_name_dict):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    if "numbers" in cell:
        cell = cell.replace("numbers", test_table_name_dict["numbers"])

    out = ip_with_dynamic_db.run_cell(cell)
    assert out.error_in_exec is None


@pytest.mark.parametrize(
    "cell",
    [
        "%%sql\nSELECT * FROM subset",
        "%%sql --with subset\nSELECT * FROM subset",
    ],
    ids=[
        "cte-inferred",
        "cte-explicit",
    ],
)
@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_mySQL",
        "ip_with_mariaDB",
        "ip_with_SQLite",
        "ip_with_duckDB_native",
        "ip_with_duckDB",
        "ip_with_MSSQL",
        "ip_with_Snowflake",
        "ip_with_oracle",
        "ip_with_clickhouse",
        "ip_with_spark",
    ],
)
def test_sql_query_cte(ip_with_dynamic_db, request, test_table_name_dict, cell):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    ip_with_dynamic_db.run_cell(
        "%%sql --save subset --no-execute \n"
        f"SELECT * FROM {test_table_name_dict['numbers']}"
    )

    out = ip_with_dynamic_db.run_cell(cell)
    assert out.error_in_exec is None


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_mySQL",
        "ip_with_mariaDB",
        "ip_with_SQLite",
        "ip_with_duckDB_native",
        "ip_with_duckDB",
        "ip_with_Snowflake",
        "ip_with_MSSQL",
        "ip_with_oracle",
        pytest.param(
            "ip_with_clickhouse",
            marks=pytest.mark.xfail(reason="Not yet implemented"),
        ),
        "ip_with_spark",
    ],
)
def test_sql_error_suggests_using_cte(ip_with_dynamic_db, request):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    with pytest.raises(UsageError) as excinfo:
        ip_with_dynamic_db.run_cell(
            """
    %%sql
S"""
        )

    assert excinfo.value.error_type == "RuntimeError"
    assert CTE_MSG in str(excinfo.value)


@pytest.mark.xfail(reason="Not yet implemented")
@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_mySQL",
        "ip_with_mariaDB",
        "ip_with_SQLite",
        "ip_with_duckDB_native",
        "ip_with_duckDB",
        "ip_with_Snowflake",
        "ip_with_MSSQL",
        "ip_with_oracle",
        "ip_with_clickhouse",
        "ip_with_spark",
    ],
)
def test_results_sets_are_closed(ip_with_dynamic_db, request, test_table_name_dict):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    ip_with_dynamic_db.run_cell(
        f"""%%sql
CREATE TABLE my_numbers AS SELECT * FROM {test_table_name_dict['numbers']}
        """
    )

    ip_with_dynamic_db.run_cell(
        """%%sql
SELECT * FROM my_numbers
        """
    )

    ip_with_dynamic_db.run_cell(
        """%%sql
DROP TABLE my_numbers
        """
    )


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_postgreSQL",
        "ip_with_mySQL",
        "ip_with_mariaDB",
        "ip_with_SQLite",
        "ip_with_duckDB_native",
        "ip_with_duckDB",
        "ip_with_Snowflake",
        "ip_with_MSSQL",
        "ip_with_oracle",
        "ip_with_clickhouse",
        "ip_with_spark",
    ],
)
@pytest.mark.parametrize(
    "cell",
    [
        "%sql SELECT * FROM __TABLE_NAME__",
        (
            "%sql WITH something AS (SELECT * FROM __TABLE_NAME__) "
            "SELECT * FROM something"
        ),
    ],
)
def test_autocommit_retrieve_existing_resultssets(
    ip_with_dynamic_db, request, test_table_name_dict, cell
):
    """
    duckdb-engine causes existing result cursor to become empty if we call
    connection.commit(), this test ensures that we correctly handle that edge
    case for duckdb and potentially other drivers.

    See: https://github.com/Mause/duckdb_engine/issues/734
    """

    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    ip_with_dynamic_db.run_cell("%config SqlMagic.autocommit=True")

    first = ip_with_dynamic_db.run_cell(
        cell.replace("__TABLE_NAME__", test_table_name_dict["numbers"])
    ).result

    second = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM {test_table_name_dict['numbers']}"
    ).result

    third = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM {test_table_name_dict['numbers']}"
    ).result

    first.fetchmany(size=1)
    second.fetchmany(size=1)
    third.fetchmany(size=1)

    assert len(first) == 60
    assert len(second) == 60
    assert len(third) == 60


@pytest.mark.parametrize(
    "ip_with_dynamic_db",
    [
        "ip_with_duckDB_native",
        "ip_with_duckDB",
    ],
)
def test_autocommit_retrieve_existing_resultssets_duckdb_from(
    ip_with_dynamic_db, request, test_table_name_dict
):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)

    ip_with_dynamic_db.run_cell("%config SqlMagic.autocommit=True")

    result = ip_with_dynamic_db.run_cell(
        f'%sql FROM {test_table_name_dict["numbers"]} LIMIT 5'
    ).result

    another = ip_with_dynamic_db.run_cell(
        f"%sql FROM {test_table_name_dict['numbers']} LIMIT 5"
    ).result

    assert len(result) == 5
    assert len(another) == 5


CREATE_TABLE = "CREATE TABLE __TABLE_NAME__ (number INT)"
CREATE_TEMP_TABLE = "CREATE TEMP TABLE __TABLE_NAME__ (number INT)"
CREATE_TEMPORARY_TABLE = "CREATE TEMPORARY TABLE __TABLE_NAME__ (number INT)"
CREATE_GLOBAL_TEMPORARY_TABLE = (
    "CREATE GLOBAL TEMPORARY TABLE __TABLE_NAME__ (number INT)"
)


@pytest.mark.parametrize(
    "ip_with_dynamic_db, create_table_statement",
    [
        ("ip_with_postgreSQL", CREATE_TABLE),
        ("ip_with_postgreSQL", CREATE_TEMP_TABLE),
        ("ip_with_mySQL", CREATE_TABLE),
        ("ip_with_mySQL", CREATE_TEMPORARY_TABLE),
        ("ip_with_mariaDB", CREATE_TABLE),
        ("ip_with_mariaDB", CREATE_TEMPORARY_TABLE),
        ("ip_with_SQLite", CREATE_TABLE),
        ("ip_with_SQLite", CREATE_TEMP_TABLE),
        ("ip_with_duckDB", CREATE_TABLE),
        ("ip_with_duckDB", CREATE_TEMP_TABLE),
        ("ip_with_duckDB_native", CREATE_TABLE),
        pytest.param(
            "ip_with_duckDB_native",
            CREATE_TEMP_TABLE,
            marks=pytest.mark.xfail(
                reason="We're executing operations in different cursors"
            ),
        ),
        ("ip_with_MSSQL", CREATE_TABLE),
        pytest.param(
            "ip_with_MSSQL",
            CREATE_TEMP_TABLE,
            marks=pytest.mark.xfail(reason="We need to fix the create table statement"),
        ),
        pytest.param(
            "ip_with_oracle",
            CREATE_TABLE,
            marks=pytest.mark.xfail(reason="Not working yet"),
        ),
        pytest.param(
            "ip_with_oracle",
            CREATE_GLOBAL_TEMPORARY_TABLE,
            marks=pytest.mark.xfail(reason="Not working yet"),
        ),
        ("ip_with_Snowflake", CREATE_TABLE),
        ("ip_with_Snowflake", CREATE_TEMPORARY_TABLE),
        pytest.param(
            "ip_with_clickhouse",
            CREATE_TABLE,
            marks=pytest.mark.xfail(reason="Not working yet"),
        ),
        ("ip_with_spark", CREATE_TABLE),
    ],
)
def test_autocommit_create_table_single_cell(
    ip_with_dynamic_db,
    request,
    create_table_statement,
):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    ip_with_dynamic_db.run_cell("%config SqlMagic.autocommit=True")
    __TABLE_NAME__ = f"table_{str(uuid4())[:8]}"

    create_table_statement = create_table_statement.replace(
        "__TABLE_NAME__", __TABLE_NAME__
    )

    result = ip_with_dynamic_db.run_cell(
        f"""%%sql
{create_table_statement};
INSERT INTO {__TABLE_NAME__} (number) VALUES (1), (2), (3);
SELECT * FROM {__TABLE_NAME__};
"""
    ).result

    assert len(result) == 3


@pytest.mark.parametrize(
    "ip_with_dynamic_db, create_table_statement",
    [
        ("ip_with_postgreSQL", CREATE_TABLE),
        ("ip_with_postgreSQL", CREATE_TEMP_TABLE),
        ("ip_with_mySQL", CREATE_TABLE),
        ("ip_with_mySQL", CREATE_TEMPORARY_TABLE),
        ("ip_with_mariaDB", CREATE_TABLE),
        ("ip_with_mariaDB", CREATE_TEMPORARY_TABLE),
        ("ip_with_SQLite", CREATE_TABLE),
        ("ip_with_SQLite", CREATE_TEMP_TABLE),
        ("ip_with_duckDB", CREATE_TABLE),
        ("ip_with_duckDB", CREATE_TEMP_TABLE),
        ("ip_with_duckDB_native", CREATE_TABLE),
        pytest.param(
            "ip_with_duckDB_native",
            CREATE_TEMP_TABLE,
            marks=pytest.mark.xfail(
                reason="We're executing operations in different cursors"
            ),
        ),
        ("ip_with_MSSQL", CREATE_TABLE),
        pytest.param(
            "ip_with_MSSQL",
            CREATE_TEMP_TABLE,
            marks=pytest.mark.xfail(
                reason="We need to close all existing result sets for this to work"
            ),
        ),
        pytest.param(
            "ip_with_oracle",
            CREATE_TABLE,
            marks=pytest.mark.xfail(reason="Not working yet"),
        ),
        pytest.param(
            "ip_with_oracle",
            CREATE_GLOBAL_TEMPORARY_TABLE,
            marks=pytest.mark.xfail(reason="Not working yet"),
        ),
        ("ip_with_Snowflake", CREATE_TABLE),
        ("ip_with_Snowflake", CREATE_TEMPORARY_TABLE),
        pytest.param(
            "ip_with_clickhouse",
            CREATE_TABLE,
            marks=pytest.mark.xfail(reason="Not working yet"),
        ),
        ("ip_with_spark", CREATE_TABLE),
    ],
)
def test_autocommit_create_table_multiple_cells(
    ip_with_dynamic_db, request, create_table_statement
):
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    ip_with_dynamic_db.run_cell("%config SqlMagic.autocommit=True")
    __TABLE_NAME__ = f"table_{str(uuid4())[:8]}"
    create_table_statement = create_table_statement.replace(
        "__TABLE_NAME__", __TABLE_NAME__
    )

    ip_with_dynamic_db.run_cell(
        f"""%%sql
{create_table_statement}
"""
    )

    ip_with_dynamic_db.run_cell(
        f"""%%sql
INSERT INTO {__TABLE_NAME__} (number) VALUES (1), (2), (3);
"""
    )

    result = ip_with_dynamic_db.run_cell(
        f"""%%sql
SELECT * FROM {__TABLE_NAME__};
"""
    ).result

    assert len(result) == 3


@pytest.mark.parametrize(
    "ip_with_dynamic_db, snippet_name, error_msgs, error_type",
    [
        (
            "ip_with_postgreSQL",
            "mysnippet",
            [
                "function not_a_function(text) does not exist",
                "No function matches the given name and argument types",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_postgreSQL",
            "mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
                'relation "mysnip" does not exist',
            ],
            "RuntimeError",
        ),
        (
            "ip_with_mySQL",
            "mysnippet",
            [
                "FUNCTION db.not_a_function does not exist",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_mySQL",
            "mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
                "Table 'db.mysnip' doesn't exist",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_mariaDB",
            "mysnippet",
            [
                "FUNCTION db.not_a_function does not exist",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_mariaDB",
            "mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
                "Table 'db.mysnip' doesn't exist",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_MSSQL",
            "mysnippet",
            [
                "not_a_function' is not a recognized built-in function name",
            ],
            "RuntimeError",
        ),
        pytest.param(
            "ip_with_MSSQL",
            "mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
            ],
            "RuntimeError",
            marks=pytest.mark.xfail(
                reason="MSSQL prioritizes function error over table error"
            ),
        ),
        (
            "ip_with_Snowflake",
            "mysnippet",
            [
                "Unknown function NOT_A_FUNCTION",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_Snowflake",
            "mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_oracle",
            "mysnippet",
            [
                '"NOT_A_FUNCTION": invalid identifier',
            ],
            "RuntimeError",
        ),
        (
            "ip_with_oracle",
            "mysnip",
            [
                "table or view does not exist",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_clickhouse",
            "mysnippet",
            [
                "Unknown function not_a_function: While processing "
                "not_a_function(taxi_driver_name)",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_clickhouse",
            "mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_redshift",
            "mysnippet",
            [
                "function not_a_function(character varying) does not exist",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_redshift",
            "mysnip",
            [
                "If using snippets, you may pass the --with argument explicitly.",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_duckDB_native",
            "mysnippet",
            [
                "Scalar Function with name not_a_function does not exist!",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_duckDB_native",
            "mysnip",
            ["Table with name mysnip does not exist!"],
            "RuntimeError",
        ),
        (
            "ip_with_spark",
            "mysnippet",
            [
                "Cannot resolve function `not_a_function` on search path",
            ],
            "RuntimeError",
        ),
        (
            "ip_with_spark",
            "mysnip",
            ["Cannot resolve function `not_a_function` on search path"],
            "RuntimeError",
        ),
    ],
    ids=[
        "no-typo-postgreSQL",
        "with-typo-postgreSQL",
        "no-typo-mySQL",
        "with-typo-mySQL",
        "no-typo-mariaDB",
        "with-typo-mariaDB",
        "no-typo-MSSQL",
        "with-typo-MSSQL",
        "no-typo-Snowflake",
        "with-typo-Snowflake",
        "no-typo-oracle",
        "with-typo-oracle",
        "no-typo-clickhouse",
        "with-typo-clickhouse",
        "no-typo-redshift",
        "with-typo-redshift",
        "no-typo-duckDB-native",
        "with-typo-duckDB-native",
        "no-typo-spark",
        "with-typo-spark",
    ],
)
def test_query_snippet_invalid_function_error_message(
    request,
    ip_with_dynamic_db,
    snippet_name,
    error_msgs,
    error_type,
    test_table_name_dict,
):
    # Set up snippet
    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    ip_with_dynamic_db.run_cell(
        f"""
        %%sql --save mysnippet
        SELECT * FROM {test_table_name_dict['taxi']}
        """
    )

    # Run query
    with pytest.raises(UsageError) as excinfo:
        ip_with_dynamic_db.run_cell(
            f"%sql SELECT not_a_function(taxi_driver_name) FROM {snippet_name}"
        )

    # Save result and test error message
    result_error = excinfo.value.error_type
    result_msg = str(excinfo.value)
    print(result_msg)
    assert error_type == result_error
    assert all(msg in result_msg for msg in error_msgs)


@pytest.mark.parametrize(
    "ip_with_dynamic_db, args",
    [
        ("ip_with_postgreSQL", ""),
        ("ip_with_duckDB", ""),
        # snowflake does not support "CREATE INDEX", so we need to
        # pass --no-index
        ("ip_with_Snowflake", "--no-index"),
        pytest.param(
            "ip_with_mySQL",
            "",
            marks=pytest.mark.xfail(reason="Access denied for user"),
        ),
        pytest.param(
            "ip_with_mariaDB",
            "",
            marks=pytest.mark.xfail(reason="Access denied for user"),
        ),
        pytest.param(
            "ip_with_SQLite", "", marks=pytest.mark.xfail(reason="schema not supported")
        ),
        pytest.param(
            "ip_with_duckDB_native",
            "",
            marks=pytest.mark.xfail(
                reason="'duckdb.DuckDBPyConnection' object has no attribute 'rowcount'"
            ),
        ),
        pytest.param(
            "ip_with_redshift",
            "",
            marks=pytest.mark.xfail(reason="permission denied for database dev"),
        ),
        pytest.param(
            "ip_with_clickhouse",
            "",
            marks=pytest.mark.xfail(
                reason="sqlalchemy.exc.CompileError: "
                "No engine for table <table_name>"
            ),
        ),
        ("ip_with_spark", "--no-index"),
    ],
)
def test_persist_in_schema(ip_with_dynamic_db, args, request, test_table_name_dict):
    limit = 15
    expected = 15

    ip_with_dynamic_db = request.getfixturevalue(ip_with_dynamic_db)
    # Clean up

    ip_with_dynamic_db.run_cell("%config SqlMagic.displaylimit = 0")

    ip_with_dynamic_db.run_cell("%sql CREATE SCHEMA IF NOT EXISTS schema1;")

    ip_with_dynamic_db.run_cell(
        f"%sql DROP TABLE IF EXISTS "
        f"schema1.{test_table_name_dict['new_table_from_df']}"
    )

    # Prepare DF
    ip_with_dynamic_db.run_cell(
        f"results = %sql SELECT * FROM {test_table_name_dict['taxi']}\
          LIMIT {limit}"
    )

    # Prepare expected df
    expected_df = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM {test_table_name_dict['taxi']}\
          LIMIT {limit}"
    )

    ip_with_dynamic_db.run_cell(
        f"{test_table_name_dict['new_table_from_df']} = results.DataFrame()"
    )
    # Create table from DF
    persist_out = ip_with_dynamic_db.run_cell(
        f"%sql --persist schema1.{test_table_name_dict['new_table_from_df']} {args}"
    )
    out_df = ip_with_dynamic_db.run_cell(
        f"%sql SELECT * FROM schema1.{test_table_name_dict['new_table_from_df']}"
    )
    assert persist_out.error_in_exec is None and out_df.error_in_exec is None
    assert len(out_df.result) == expected

    expected_df_ = expected_df.result.DataFrame()
    out_df_ = out_df.result.DataFrame()

    assert expected_df_.equals(out_df_.loc[:, out_df_.columns != "level_0"])
