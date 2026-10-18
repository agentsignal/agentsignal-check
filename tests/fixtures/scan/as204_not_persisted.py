from agents import Agent, Runner, function_tool


@function_tool(needs_approval=True)
def delete_file(path: str) -> str:
    """Delete a file."""
    return f"would delete {path}"


async def main():
    agent = Agent(name="ops", tools=[delete_file])
    r = await Runner.run(agent, "clean up")
    for it in r.interruptions:
        print("pending", it.tool_name)
