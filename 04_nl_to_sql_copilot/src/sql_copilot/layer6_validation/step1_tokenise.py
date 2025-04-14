"""
LAYER 6 - VALIDATION, STEP 1: BREAKING SQL INTO TOKENS
======================================================
Before anything can be checked, the SQL has to be read properly. Every shortcut
here is a hole.

------------------------------------------------------------------------------
WHY NOT JUST SEARCH THE TEXT FOR "DROP"?
------------------------------------------------------------------------------
Because these are all wrong:

    "update" in sql.lower()
        blocks   SELECT updated_at FROM orders
        - a perfectly ordinary column name. Do this and your validator refuses
          half the legitimate queries, somebody loosens it, and then it refuses
          nothing.

    ";" in sql
        blocks   SELECT * FROM orders WHERE note = 'paid; refunded'
        - a semicolon inside a string literal is data, not a statement break.

    "drop" not in sql
        allows   SELECT * FROM orders WHERE x = 1; DROP TABLE orders
                 when the check ran on only the first line, or on a version
                 before comments were stripped

So the SQL is tokenised: string literals become single tokens, comments are
removed, and identifiers are compared whole. `updated_at` is one word and is not
`update`. A semicolon inside a string is inside a string.

This file does not decide anything. It only reads. Step 2 decides.
"""

from enum import Enum


class TokenKind(str, Enum):
    WORD = "word"          # identifier or keyword
    STRING = "string"      # 'a literal'
    NUMBER = "number"
    PUNCTUATION = "punctuation"
    OPERATOR = "operator"


class Token:
    def __init__(self, kind: TokenKind, text: str, position: int) -> None:
        self.kind = kind
        self.text = text
        self.position = position

    def word(self) -> str:
        """The lowercase text, for comparing keywords and identifiers."""
        return self.text.lower()

    def __repr__(self) -> str:
        return "Token(%s, %r)" % (self.kind.value, self.text)


class TokeniseResult:
    def __init__(
        self,
        tokens: list[Token],
        comments: list[str],
        unterminated_string: bool,
        comment_spans: list[tuple[int, int]] | None = None,
    ) -> None:
        self.tokens = tokens
        self.comments = comments
        if comment_spans is None:
            comment_spans = []
        # Where each comment sat in the original text, so it can be cut out
        # without rebuilding the query from tokens.
        self.comment_spans = comment_spans
        # An unterminated quote means the rest of the statement was swallowed
        # into a string. Whatever the query does, it is not what it looks like.
        self.unterminated_string = unterminated_string


WORD_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
WORD_BODY = WORD_START + "0123456789$"
DIGITS = "0123456789"
PUNCTUATION = "(),;."
OPERATOR_CHARACTERS = "+-*/%<>=!|&~^"


def tokenise(sql: str) -> TokeniseResult:
    """
    Read SQL into tokens, with comments removed and strings kept whole.

    Handles the quoting styles that matter:
        'text'        a string literal, with '' as an escaped quote
        "identifier"  a quoted identifier
        `identifier`  MySQL style
        [identifier]  SQL Server style
    """
    tokens: list[Token] = []
    comments: list[str] = []
    comment_spans: list[tuple[int, int]] = []
    unterminated = False

    position = 0
    length = len(sql)

    while position < length:
        character = sql[position]

        # --- whitespace ---
        if character in " \t\r\n":
            position = position + 1
            continue

        # --- line comment ---
        if character == "-" and position + 1 < length and sql[position + 1] == "-":
            end = sql.find("\n", position)
            if end == -1:
                end = length
            comments.append(sql[position:end])
            comment_spans.append((position, end))
            position = end
            continue

        # --- block comment ---
        if character == "/" and position + 1 < length and sql[position + 1] == "*":
            end = sql.find("*/", position + 2)
            if end == -1:
                comments.append(sql[position:])
                comment_spans.append((position, length))
                position = length
            else:
                comments.append(sql[position : end + 2])
                comment_spans.append((position, end + 2))
                position = end + 2
            continue

        # --- string literal ---
        if character == "'":
            start = position
            position = position + 1
            closed = False

            while position < length:
                if sql[position] == "'":
                    # Two quotes in a row is an escaped quote, not the end.
                    if position + 1 < length and sql[position + 1] == "'":
                        position = position + 2
                        continue
                    closed = True
                    position = position + 1
                    break
                position = position + 1

            if not closed:
                unterminated = True

            tokens.append(Token(TokenKind.STRING, sql[start:position], start))
            continue

        # --- quoted identifier ---
        if character in '"`[':
            if character == "[":
                closing = "]"
            else:
                closing = character

            start = position
            end = sql.find(closing, position + 1)
            if end == -1:
                unterminated = True
                tokens.append(Token(TokenKind.WORD, sql[start:], start))
                position = length
                continue

            inner = sql[start + 1 : end]
            tokens.append(Token(TokenKind.WORD, inner, start))
            position = end + 1
            continue

        # --- number ---
        if character in DIGITS:
            start = position
            while position < length and (sql[position] in DIGITS or sql[position] == "."):
                position = position + 1
            tokens.append(Token(TokenKind.NUMBER, sql[start:position], start))
            continue

        # --- word ---
        if character in WORD_START:
            start = position
            while position < length and sql[position] in WORD_BODY:
                position = position + 1
            tokens.append(Token(TokenKind.WORD, sql[start:position], start))
            continue

        # --- punctuation ---
        if character in PUNCTUATION:
            tokens.append(Token(TokenKind.PUNCTUATION, character, position))
            position = position + 1
            continue

        # --- operator ---
        if character in OPERATOR_CHARACTERS:
            start = position
            while position < length and sql[position] in OPERATOR_CHARACTERS:
                position = position + 1
            tokens.append(Token(TokenKind.OPERATOR, sql[start:position], start))
            continue

        # --- anything else, one character at a time ---
        tokens.append(Token(TokenKind.PUNCTUATION, character, position))
        position = position + 1

    return TokeniseResult(
        tokens=tokens,
        comments=comments,
        unterminated_string=unterminated,
        comment_spans=comment_spans,
    )


def count_statements(tokens: list[Token]) -> int:
    """
    How many statements this is.

    Semicolons inside strings were already absorbed into STRING tokens, so only
    real statement separators are counted here. A trailing semicolon does not
    make a second statement.
    """
    statements = 1
    seen_content_since_semicolon = False

    for token in tokens:
        if token.kind == TokenKind.PUNCTUATION and token.text == ";":
            seen_content_since_semicolon = False
            continue

        if not seen_content_since_semicolon:
            seen_content_since_semicolon = True
            # This is content after a semicolon that already had content before
            # it, which means a second statement.
            if any_semicolon_before(tokens, token):
                statements = statements + 1

    return statements


def any_semicolon_before(tokens: list[Token], target: Token) -> bool:
    for token in tokens:
        if token is target:
            return False
        if token.kind == TokenKind.PUNCTUATION and token.text == ";":
            return True
    return False


def parentheses_are_balanced(tokens: list[Token]) -> bool:
    depth = 0
    for token in tokens:
        if token.kind != TokenKind.PUNCTUATION:
            continue
        if token.text == "(":
            depth = depth + 1
        elif token.text == ")":
            depth = depth - 1
            if depth < 0:
                return False
    return depth == 0


def strip_comments(sql: str) -> str:
    """
    Return the SQL with its comments cut out, and nothing else changed.

    WHY IT CUTS RATHER THAN REBUILDS. The first version reassembled the query
    from its tokens, which quietly reformatted it: "WITH recent AS (SELECT" came
    back as "WITH recent AS(SELECT". Still valid, but the text being executed was
    no longer the text a person had read, and reformatting SQL you are about to
    run is a habit with no upside.

    Removing the comment spans from the original leaves every other character
    exactly where the model put it.
    """
    result = tokenise(sql)

    if len(result.comment_spans) == 0:
        return sql.strip()

    kept: list[str] = []
    previous_end = 0

    for start, end in result.comment_spans:
        kept.append(sql[previous_end:start])
        # A comment separated two tokens, so it leaves a space behind it.
        kept.append(" ")
        previous_end = end

    kept.append(sql[previous_end:])

    joined = "".join(kept)

    # Collapse the runs of whitespace the removals leave behind.
    collapsed: list[str] = []
    in_string = False
    position = 0

    while position < len(joined):
        character = joined[position]

        if character == "'":
            in_string = not in_string
            collapsed.append(character)
            position = position + 1
            continue

        if not in_string and character in " \t\r\n":
            collapsed.append(" ")
            while position < len(joined) and joined[position] in " \t\r\n":
                position = position + 1
            continue

        collapsed.append(character)
        position = position + 1

    return "".join(collapsed).strip()
