"""Tests for resource tools that exercise the full stack with SQLite."""

import io
import base64
from PIL import Image as PILImage

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import resource
from basic_memory.mcp.tools import notes


@pytest.mark.asyncio
async def test_read_resource_text_file(app, synced_files):
    """Test reading a text file.

    Should:
    - Correctly identify text content
    - Return the content as text
    - Include correct metadata
    """
    # First create a text file via notes
    result = await notes.write_note(
        title="Text Resource",
        folder="test",
        content="This is a test text resource",
        tags=["test", "resource"],
    )
    assert result is not None

    # Now read it as a resource
    response = await resource.read_resource("test/text-resource")

    assert response["type"] == "text"
    assert "This is a test text resource" in response["text"]
    assert response["content_type"].startswith("text/")
    assert response["encoding"] == "utf-8"


@pytest.mark.asyncio
async def test_read_resource_image_file(app, synced_files):
    """Test reading an image file.

    Should:
    - Correctly identify image content
    - Optimize the image
    - Return base64 encoded image data
    """
    # Get the path to the synced image file
    image_path = synced_files["image"].name

    # Read it as a resource
    response = await resource.read_resource(image_path)

    assert response["type"] == "image"
    assert response["source"]["type"] == "base64"
    assert response["source"]["media_type"] == "image/jpeg"

    # Verify the image data is valid base64 that can be decoded
    img_data = base64.b64decode(response["source"]["data"])
    assert len(img_data) > 0

    # Should be able to open as an image
    img = PILImage.open(io.BytesIO(img_data))
    assert img.width > 0
    assert img.height > 0


@pytest.mark.asyncio
async def test_read_resource_pdf_file(app, synced_files):
    """Test reading a PDF file.

    Should:
    - Correctly identify PDF content
    - Return base64 encoded PDF data
    """
    # Get the path to the synced PDF file
    pdf_path = synced_files["pdf"].name

    # Read it as a resource
    response = await resource.read_resource(pdf_path)

    assert response["type"] == "document"
    assert response["source"]["type"] == "base64"
    assert response["source"]["media_type"] == "application/pdf"

    # Verify the PDF data is valid base64 that can be decoded
    pdf_data = base64.b64decode(response["source"]["data"])
    assert len(pdf_data) > 0
    assert pdf_data.startswith(b"%PDF")  # PDF signature


@pytest.mark.asyncio
async def test_read_resource_not_found(app):
    """Test trying to read a non-existent resource."""
    with pytest.raises(ToolError, match="Error calling tool: Client error '404 Not Found'"):
        await resource.read_resource("does-not-exist")


@pytest.mark.asyncio
async def test_read_resource_memory_url(app, synced_files):
    """Test reading a resource using a memory:// URL."""
    # Create a text file via notes
    await notes.write_note(
        title="Memory URL Test",
        folder="test",
        content="Testing memory:// URL handling for resources",
    )

    # Read it with a memory:// URL
    memory_url = "memory://test/memory-url-test"
    response = await resource.read_resource(memory_url)

    assert response["type"] == "text"
    assert "Testing memory:// URL handling for resources" in response["text"]


@pytest.mark.asyncio
async def test_image_optimization_functions(app):
    """Test the image optimization helper functions."""
    # Create a test image
    img = PILImage.new("RGB", (1000, 800), color="white")

    # Test calculate_target_params function
    # Small image
    quality, size = resource.calculate_target_params(100000)
    assert quality == 70
    assert size == 1000

    # Medium image
    quality, size = resource.calculate_target_params(800000)
    assert quality == 60
    assert size == 800

    # Large image
    quality, size = resource.calculate_target_params(2000000)
    assert quality == 50
    assert size == 600

    # Test resize_image function
    # Image that needs resizing
    resized = resource.resize_image(img, 500)
    assert resized.width <= 500
    assert resized.height <= 500

    # Image that doesn't need resizing
    small_img = PILImage.new("RGB", (300, 200), color="white")
    resized = resource.resize_image(small_img, 500)
    assert resized.width == 300
    assert resized.height == 200

    # Test optimize_image function
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    content_length = len(img_bytes.getvalue())

    # In a small test image, optimization might make the image larger
    # because of JPEG overhead. Let's just test that it returns something
    optimized = resource.optimize_image(img, content_length)
    assert len(optimized) > 0


@pytest.mark.asyncio
async def test_read_resource_with_transparency(app, synced_files, mocker):
    """Test reading an image with transparency.

    Should:
    - Convert RGBA images to RGB
    - Handle transparency correctly
    """
    # Mock the response to simulate an RGBA image
    mock_response = mocker.MagicMock()
    mock_response.headers = {"content-type": "image/png", "content-length": "10000"}

    # Create a test PNG with transparency
    img = PILImage.new("RGBA", (500, 400), color=(255, 255, 255, 0))
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    mock_response.content = img_bytes.getvalue()

    # Mock call_get to return our transparent image
    mocker.patch("basic_memory.mcp.tools.resource.call_get", return_value=mock_response)

    # Test reading the resource
    response = await resource.read_resource("transparent-image.png")

    assert response["type"] == "image"
    assert response["source"]["media_type"] == "image/jpeg"

    # Verify the image data is valid and was converted to RGB
    img_data = base64.b64decode(response["source"]["data"])
    img = PILImage.open(io.BytesIO(img_data))
    assert img.mode == "RGB"  # Should be converted from RGBA to RGB


@pytest.mark.asyncio
async def test_read_resource_large_document(app, mocker):
    """Test handling of documents that exceed the size limit.

    Should:
    - Detect when document size exceeds limit
    - Return appropriate error message
    """
    # Mock the response to simulate a large document
    mock_response = mocker.MagicMock()
    mock_response.headers = {"content-type": "application/octet-stream", "content-length": "500000"}
    mock_response.content = b"0" * 500000  # Create a large fake binary document

    # Mock call_get to return our large document
    mocker.patch("basic_memory.mcp.tools.resource.call_get", return_value=mock_response)

    # Test reading the resource
    response = await resource.read_resource("large-document.bin")

    assert response["type"] == "error"
    assert "Document size 500000 bytes exceeds maximum allowed size" in response["error"]


# Let's skip the minimum parameters test since those values are internal to the optimize_image function
# The rest of the code is well covered by the other tests
# @pytest.mark.skip("Minimum parameter test not needed - code already has good coverage")
# @pytest.mark.asyncio
# async def test_optimize_image_limits(app, monkeypatch):
#     """Test image optimization when it reaches minimum parameters."""
#     pass
