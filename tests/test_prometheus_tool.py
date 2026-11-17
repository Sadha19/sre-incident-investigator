from src.sre_agent.tools.prometheus_tool import PrometheusTool


def test_prometheus_connection():
    tool = PrometheusTool()

    result = tool.check_connection()

    assert result is True


def test_prometheus_up_query():
    tool = PrometheusTool()

    result = tool.get_up_metrics()

    assert result["success"] is True
    assert isinstance(result["result"], list)


def test_prometheus_custom_query():
    tool = PrometheusTool()

    result = tool.query("up")

    assert result["success"] is True
    assert result["query"] == "up"


def test_prometheus_cpu_query():
    tool = PrometheusTool()

    result = tool.get_cpu_usage()

    assert result["success"] is True
    assert isinstance(result["result"], list)


def test_prometheus_memory_query():
    tool = PrometheusTool()

    result = tool.get_memory_usage()

    assert result["success"] is True
    assert isinstance(result["result"], list)


def test_pod_cpu_query():
    tool = PrometheusTool()

    result = tool.get_pod_cpu_usage()

    assert result["success"] is True
    assert isinstance(result["result"], list)


def test_pod_memory_query():
    tool = PrometheusTool()

    result = tool.get_pod_memory_usage()

    assert result["success"] is True
    assert isinstance(result["result"], list)


def test_pod_restart_query():
    tool = PrometheusTool()

    result = tool.get_pod_restarts()

    assert result["success"] is True
    assert isinstance(result["result"], list)


def test_cpu_promql():
    tool = PrometheusTool()

    result = tool.query(
        "rate(container_cpu_usage_seconds_total[5m])"
    )

    assert result["success"] is True


def test_memory_promql():
    tool = PrometheusTool()

    result = tool.query(
        "container_memory_working_set_bytes"
    )

    assert result["success"] is True


def test_restart_promql():
    tool = PrometheusTool()

    result = tool.query(
        "kube_pod_container_status_restarts_total"
    )

    assert result["success"] is True


def test_cpu_throttling_query():
    tool = PrometheusTool()

    result = tool.get_cpu_throttling()

    assert result["success"] is True
    assert isinstance(result["result"], list)


def test_pod_memory_limit_query():
    tool = PrometheusTool()

    result = tool.get_pod_memory_limits()

    assert result["success"] is True
    assert isinstance(result["result"], list)
