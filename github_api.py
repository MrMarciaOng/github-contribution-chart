"""GitHub GraphQL access through the active gh login."""

import json
import subprocess


def graphql(query):
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        check=True, capture_output=True, text=True,
    )
    response = json.loads(result.stdout)
    if response.get("errors"):
        raise RuntimeError(response["errors"])
    return response


def connection_nodes(username, field, fields, arguments=""):
    after = None
    while True:
        cursor = f",after:{json.dumps(after)}" if after is not None else ""
        query = (
            "query { user(login:" + json.dumps(username) + ") { "
            + field + "(first:100" + arguments + cursor + ") { nodes { "
            + fields + " } pageInfo { hasNextPage endCursor } } } }"
        )
        connection = graphql(query)["data"]["user"][field]
        yield from connection["nodes"]
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            return
        if not page["endCursor"] or page["endCursor"] == after:
            raise RuntimeError(f"GitHub did not advance pagination for {field}")
        after = page["endCursor"]
