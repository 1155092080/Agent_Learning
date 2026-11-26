from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

SAMPLE_BYTES = 4096
SAMPLES = 32
MAX_RETAINED = 128

mcp = FastMCP("memory-retention-lab")
retained: list[bytearray] = []
retain_samples = False


class SampleReport(BaseModel):
    verdict: Literal["CLEAR", "GROWING", "AT_CAP"]
    processed: int
    checksum: int
    retained_samples: int
    retained_payload_bytes: int
    growth_payload_bytes: int


@mcp.tool()
def inject_leak() -> str:
    """Enable bounded retention for future samples in this toy process only."""
    global retain_samples
    retain_samples = True
    return "Retention enabled"


@mcp.tool()
def probe_leak() -> SampleReport:
    """Process a fixed batch and report toy retained payload, not process RSS."""
    before = len(retained)
    checksum = 0
    for index in range(SAMPLES):
        sample = bytearray(SAMPLE_BYTES)
        sample[0] = index + 1
        checksum += sample[0]
        if retain_samples and len(retained) < MAX_RETAINED:
            retained.append(sample)

    count = len(retained)
    verdict = "CLEAR" if count == 0 else "GROWING" if count > before else "AT_CAP"
    return SampleReport(
        verdict=verdict,
        processed=SAMPLES,
        checksum=checksum,
        retained_samples=count,
        retained_payload_bytes=count * SAMPLE_BYTES,
        growth_payload_bytes=(count - before) * SAMPLE_BYTES,
    )


@mcp.tool()
def fix_leak() -> str:
    """Clear retained samples and stop retaining new ones in the toy process."""
    global retain_samples
    retain_samples = False
    retained.clear()
    return "Retention disabled; held samples cleared"


if __name__ == "__main__":
    mcp.run(transport="stdio")
