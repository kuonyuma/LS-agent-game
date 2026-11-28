from collections.abc import Mapping

from google.genai import types

from agent_service.adapters.research.service import AcademicResearchService
from agent_service.adapters.security.workspace import WorkspacePathPolicy
from agent_service.adapters.tools.academic_search import (
    InspectPaperTool,
    ReadPaperFulltextTool,
    SearchPapersTool,
)
from agent_service.adapters.tools.calculate import CalculateTool
from agent_service.adapters.tools.calendar_tools import DateCalcTool, GetCurrentTimeTool
from agent_service.adapters.tools.edit_file import EditFileTool
from agent_service.adapters.tools.exchange_rate import GetExchangeRateTool
from agent_service.adapters.tools.fetch_web_page import FetchWebPageTool
from agent_service.adapters.tools.list_files import ListFilesTool
from agent_service.adapters.tools.maps import (
    MapsService,
    PlanRouteTool,
    SearchPlacesTool,
)
from agent_service.adapters.tools.plan_tool import UpdatePlanTool
from agent_service.adapters.tools.quality_check import QualityCheckTool
from agent_service.adapters.tools.read_file import ReadFileTool
from agent_service.adapters.tools.reflect_tool import ReflectTool
from agent_service.adapters.tools.run_command import RunCommandTool
from agent_service.adapters.tools.search_images import SearchImagesTool
from agent_service.adapters.tools.subagent_tool import SubagentTool
from agent_service.adapters.tools.weather import GetWeatherTool
from agent_service.adapters.tools.web_search import WebSearchTool
from agent_service.adapters.tools.write_file import WriteFileTool
from agent_service.adapters.tools.yaml_loader import LoadYamlTool
from agent_service.kernel.dispatch.base import Tool


def create_tool_registry(
    workspace: WorkspacePathPolicy | None = None,
    *,
    deepseek_api_key: str | None = None,
    serper_api_key: str | None = None,
    tavily_api_key: str | None = None,
    knowledge_tool: Tool | None = None,
    presentation_tool: Tool | None = None,
    image_tool: Tool | None = None,
    default_timezone: str = "UTC",
    allow_deepseek_fetch: bool = True,
) -> dict[str, Tool]:
    """每次组装创建独立工具实例，按需注入外部能力。"""
    research_service = AcademicResearchService()
    maps_service = MapsService()
    tools: list[Tool] = [
        ListFilesTool(workspace),
        ReadFileTool(workspace),
        WriteFileTool(workspace),
        EditFileTool(workspace),
        LoadYamlTool(workspace),
        RunCommandTool(workspace),
        UpdatePlanTool(),
        ReflectTool(),
        SubagentTool(),
        GetCurrentTimeTool(default_timezone),
        DateCalcTool(),
        CalculateTool(),
        GetWeatherTool(default_open_meteo=workspace is not None),
        GetExchangeRateTool(),
        SearchPlacesTool(maps_service),
        PlanRouteTool(maps_service),
        WebSearchTool(deepseek_api_key=deepseek_api_key),
        FetchWebPageTool(
            deepseek_api_key=deepseek_api_key,
            allow_deepseek=allow_deepseek_fetch,
        ),
        SearchImagesTool(
            serper_api_key=serper_api_key,
            tavily_api_key=tavily_api_key,
        ),
        SearchPapersTool(research_service),
        InspectPaperTool(research_service),
        ReadPaperFulltextTool(research_service),
    ]
    if workspace is not None:
        tools.append(QualityCheckTool(workspace))
    if knowledge_tool is not None:
        tools.append(knowledge_tool)
    if presentation_tool is not None:
        tools.append(presentation_tool)
    if image_tool is not None:
        tools.append(image_tool)
    registry = {tool.name: tool for tool in tools}
    if len(registry) != len(tools):
        raise ValueError("工具注册失败：工具名称不能重复")
    return registry


def get_function_declarations(registry: Mapping[str, Tool]) -> list[types.Tool]:
    declarations = [tool.to_function_declaration() for tool in registry.values()]
    return [types.Tool(function_declarations=declarations)] if declarations else []
