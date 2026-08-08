from mythic_container.MythicCommandBase import *
from mythic_container.MythicRPC import *


class ExitArguments(TaskArguments):
    def __init__(self, command_line, **kwargs):
        super().__init__(command_line, **kwargs)
        self.args = []

    async def parse_arguments(self):
        pass


class ExitCommand(CommandBase):
    cmd = "exit"
    needs_admin = False
    help_cmd = "exit"
    description = "Exit the callback and remove it from active callbacks"
    version = 1
    author = "@operator"
    argument_class = ExitArguments
    attackmapping = []
    supported_ui_features = ["callback_table:exit"]
    attributes = CommandAttributes(
        builtin=True,
        suggested_command=False
    )

    async def create_go_tasking(self, taskData: PTTaskMessageAllData) -> PTTaskCreateTaskingMessageResponse:
        """Mark the callback as exited"""
        response = PTTaskCreateTaskingMessageResponse(
            TaskID=taskData.Task.ID,
            Success=True,
        )

        try:
            # Mark callback as exited
            exit_resp = await SendMythicRPCCallbackUpdate(
                MythicRPCCallbackUpdateMessage(
                    CallbackID=taskData.Callback.ID,
                    Active=False
                )
            )

            if exit_resp.Success:
                response.TaskStatus = MythicStatus.Completed
                response.Completed = True
                await SendMythicRPCResponseCreate(
                    MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=b"Callback marked as inactive. Goodbye!"
                    )
                )
            else:
                response.TaskStatus = MythicStatus.Error
                response.Completed = True
                await SendMythicRPCResponseCreate(
                    MythicRPCResponseCreateMessage(
                        TaskID=taskData.Task.ID,
                        Response=f"Failed to exit callback: {exit_resp.Error}".encode()
                    )
                )

        except Exception as e:
            response.Success = False
            response.TaskStatus = MythicStatus.Error
            response.Completed = True
            error_msg = f"Error exiting callback: {str(e)}"
            await SendMythicRPCResponseCreate(
                MythicRPCResponseCreateMessage(
                    TaskID=taskData.Task.ID,
                    Response=error_msg.encode()
                )
            )

        return response

    async def process_response(self, task: PTTaskMessageAllData, response: any) -> PTTaskProcessResponseMessageResponse:
        resp = PTTaskProcessResponseMessageResponse(TaskID=task.Task.ID, Success=True)
        return resp
