"""
TESTS FOR THE TOKENISER.

Each of these is a way a text-matching validator gets bypassed, or a legitimate
query it wrongly refuses.
"""

from sql_copilot.layer6_validation.step1_tokenise import (
    TokenKind,
    count_statements,
    parentheses_are_balanced,
    strip_comments,
    tokenise,
)


def test_a_column_containing_a_keyword_is_one_word():
    """
    'updated_at' contains 'update'. A substring check refuses every query that
    mentions it, somebody loosens the check, and then it refuses nothing.
    """
    result = tokenise("SELECT updated_at FROM orders")

    words = []
    for token in result.tokens:
        if token.kind == TokenKind.WORD:
            words.append(token.word())

    assert "updated_at" in words
    assert "update" not in words


def test_a_semicolon_inside_a_string_is_not_a_statement_break():
    result = tokenise("SELECT * FROM orders WHERE note = 'paid; refunded'")
    assert count_statements(result.tokens) == 1


def test_a_real_stacked_statement_is_counted():
    result = tokenise("SELECT * FROM orders; DROP TABLE orders")
    assert count_statements(result.tokens) == 2


def test_a_trailing_semicolon_is_not_a_second_statement():
    result = tokenise("SELECT * FROM orders;")
    assert count_statements(result.tokens) == 1


def test_comments_are_removed_along_with_what_they_hide():
    result = tokenise("SELECT * FROM orders -- ; DROP TABLE orders")
    assert count_statements(result.tokens) == 1
    assert len(result.comments) == 1
    assert "DROP" not in strip_comments("SELECT * FROM orders -- ; DROP TABLE orders")


def test_block_comments_are_removed():
    cleaned = strip_comments("SELECT a, /* ; DELETE FROM x */ b FROM orders")
    assert "DELETE" not in cleaned
    assert "SELECT a," in cleaned


def test_an_escaped_quote_does_not_end_the_string():
    result = tokenise("SELECT * FROM customers WHERE name = 'O''Brien'")
    assert result.unterminated_string is False
    assert count_statements(result.tokens) == 1


def test_an_unterminated_quote_is_flagged():
    """Everything after the quote is inside a string, so the query is not what it looks like."""
    result = tokenise("SELECT * FROM orders WHERE x = 'open")
    assert result.unterminated_string is True


def test_unbalanced_parentheses_are_detected():
    assert parentheses_are_balanced(tokenise("SELECT (a + b) FROM t").tokens) is True
    assert parentheses_are_balanced(tokenise("SELECT (a + b FROM t").tokens) is False


def test_stripping_comments_does_not_reformat_the_query():
    """
    The text that runs must be the text a person read. An early version rebuilt
    the query from tokens and turned "AS (SELECT" into "AS(SELECT".
    """
    original = "WITH recent AS (SELECT * FROM orders) SELECT COUNT(*) FROM recent"
    assert strip_comments(original) == original


def test_quoted_identifiers_are_read_as_words():
    result = tokenise('SELECT "order_id" FROM orders')

    words = []
    for token in result.tokens:
        if token.kind == TokenKind.WORD:
            words.append(token.word())
    assert "order_id" in words
