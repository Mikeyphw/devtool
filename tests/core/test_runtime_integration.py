from devtool.core import CommandResult, result_from_exception, result_to_dict


def test_result_serialization():
    result = CommandResult(
        success=True,
        exit_code=0,
        stdout="ok",
        stderr="",
        duration=1.0,
        warnings=[],
        errors=[],
        metadata={},
    )
    assert result_to_dict(result)["success"] is True


def test_exception_conversion():
    result = result_from_exception(RuntimeError("failure"))
    assert result.success is False
    assert "failure" in result.stderr
