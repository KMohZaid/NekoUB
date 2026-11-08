from core.plugins import Plugin, handlers as h


class PrivateExample(Plugin):
    id = "private_example"

    @h.command("private", description="Confirm private plugin loading.")
    async def private(self, client, message, ctx, args):
        await ctx.edit("I am from private plugin", message=message)
