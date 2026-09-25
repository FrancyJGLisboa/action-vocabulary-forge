  const agent = new ToolLoopAgent({
    model: anthropic('claude-sonnet-5'),
    instructions: systemInstruction ?? defaultInstructions,
    tools: { weather: weatherTool },
  });
