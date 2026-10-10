const vscode = require("vscode");

exports.activate = (context) => {
  let count = 0;
  context.subscriptions.push(
    vscode.commands.registerCommand("rust-glancer.reindexWorkspace", () => ++count),
  );
};
