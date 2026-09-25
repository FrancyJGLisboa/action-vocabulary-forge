  const { output } = await generateText({
    model: openai('gpt-6-astra'),
    instructions: 'You are a helpful assistant.',
    prompt,
    output: Output.object({
      schema: z.object({
        notifications: z.array(
          z.object({
