import * as assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  hoverAction,
  hoverActionLinkLine,
  locationsExcludingCurrentHover,
  protocolDefinitionLocations,
  uniqueLocations,
  type SerializedLocation,
  type HoverOrigin,
} from "../src/features/hover-actions-model";
import { EXTENSION_COMMANDS } from "../src/commands";

describe("hover action model", () => {
  it("normalizes protocol locations and removes duplicate targets", () => {
    const declaration = location("file:///src/lib.rs", 1, 4, 1, 10);
    const linkedSelection = location("file:///src/types.rs", 3, 8, 3, 14);

    const actual = uniqueLocations(
      protocolDefinitionLocations([
        declaration,
        declaration,
        {
          targetUri: "file:///src/types.rs",
          targetRange: range(3, 0, 3, 20),
          targetSelectionRange: linkedSelection.range,
        },
      ]),
    );

    assert.deepEqual(actual, [declaration, linkedSelection]);
  });

  it("filters hover self-links without hiding other locations", () => {
    const self = location("file:///src/lib.rs", 1, 4, 1, 10);
    const sameFileDifferentRange = location("file:///src/lib.rs", 2, 4, 2, 10);
    const otherFile = location("file:///src/types.rs", 1, 4, 1, 10);

    assert.deepEqual(
      locationsExcludingCurrentHover(
        [self, sameFileDifferentRange, otherFile],
        "file:///src/lib.rs",
        self.range,
      ),
      [sameFileDifferentRange, otherFile],
    );
  });

  it("encodes the originating document session and revision for deferred navigation", () => {
    const origin: HoverOrigin = {
      uri: "file:///src/lib.rs",
      position: { line: 4, character: 8 },
      range: range(4, 8, 4, 12),
      version: 7,
      session: "opened-document-session",
    };
    const actions = [
      hoverAction(EXTENSION_COMMANDS.goToTypeFromHover, "type", origin),
      hoverAction(EXTENSION_COMMANDS.goToImplementationFromHover, "implementation", origin),
    ];

    const actual = hoverActionLinkLine(actions);

    assert.deepEqual(actual.enabledCommands, [
      EXTENSION_COMMANDS.goToTypeFromHover,
      EXTENSION_COMMANDS.goToImplementationFromHover,
    ]);
    const links = [
      ...actual.markdown.matchAll(/\[(type|implementation)\]\((command:[^\s]+) "([^"]+)"\)/g),
    ];
    assert.deepEqual(
      links.map((link) => [link[1], link[3]]),
      [
        ["type", "Go to type"],
        ["implementation", "Go to implementation"],
      ],
    );
    for (const [index, link] of links.entries()) {
      const target = new URL(link[2]);
      assert.equal(target.protocol, "command:");
      assert.equal(target.pathname, actions[index].command);
      assert.deepEqual(JSON.parse(decodeURIComponent(target.search.slice(1))), [origin]);
    }
  });
});

function location(
  uri: string,
  startLine: number,
  startCharacter: number,
  endLine: number,
  endCharacter: number,
): SerializedLocation {
  return {
    uri,
    range: range(startLine, startCharacter, endLine, endCharacter),
  };
}

function range(
  startLine: number,
  startCharacter: number,
  endLine: number,
  endCharacter: number,
): SerializedLocation["range"] {
  return {
    start: { line: startLine, character: startCharacter },
    end: { line: endLine, character: endCharacter },
  };
}
