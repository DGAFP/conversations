import { CunninghamProvider } from '@gouvfr-lasuite/cunningham-react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { LLMModel } from '@/features/chat/api/useLLMConfiguration';
import '@/i18n/initI18n';

import { InputChatActions } from '../InputChatAction';
import { ModelSelector } from '../ModelSelector';

const configuration = vi.hoisted(() => ({
  models: [] as LLMModel[],
  isLoading: false,
}));

vi.mock('@/features/chat/api/useLLMConfiguration', () => ({
  useLLMConfiguration: () => ({
    data: { models: configuration.models },
    isLoading: configuration.isLoading,
  }),
}));

const models: LLMModel[] = ['matte', 'masa', 'mi', 'mso'].map((ministry) => ({
  hrid: `assistant-rh-${ministry}`,
  human_readable_name: `Ministère ${ministry.toUpperCase()}`,
  icon: `/assets/${ministry}.svg`,
  is_default: ministry === 'matte',
  model_name: `assistant-rh-${ministry}`,
}));

describe('ModelSelector', () => {
  beforeEach(() => {
    configuration.models = models;
    configuration.isLoading = false;
  });

  it('shows the default ministry, its logo and the collapsed state', () => {
    render(<ModelSelector selectedModel={null} onModelSelect={vi.fn()} />);

    const button = screen.getByRole('button', {
      name: 'Select model: Ministère MATTE',
    });
    expect(button).toHaveTextContent('MATTE');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(
      screen.getByRole('img', { name: 'Ministère MATTE' }),
    ).toHaveAttribute('src', '/assets/matte.svg');
  });

  it.each(models)('updates the label when selecting $hrid', async (model) => {
    const user = userEvent.setup();
    const onModelSelect = vi.fn();
    const { rerender } = render(
      <ModelSelector selectedModel={models[0]} onModelSelect={onModelSelect} />,
    );

    await user.click(
      screen.getByRole('button', { name: 'Select model: Ministère MATTE' }),
    );
    expect(
      screen.getByRole('button', { name: 'Select model: Ministère MATTE' }),
    ).toHaveAttribute('aria-expanded', 'true');
    await user.click(
      screen.getByRole('button', {
        name: `Select ${model.human_readable_name}`,
      }),
    );
    expect(onModelSelect).toHaveBeenCalledTimes(1);
    expect(onModelSelect).toHaveBeenCalledWith(model);

    rerender(
      <ModelSelector selectedModel={model} onModelSelect={onModelSelect} />,
    );
    const button = screen.getByRole('button', {
      name: `Select model: ${model.human_readable_name}`,
    });
    expect(button).toHaveTextContent(
      model.hrid.replace('assistant-rh-', '').toUpperCase(),
    );
    expect(button).toHaveAttribute('aria-expanded', 'false');
  });

  it('uses the configured display name for other models', () => {
    const model = {
      ...models[0],
      hrid: 'other-model',
      human_readable_name: 'Other model',
    };
    configuration.models = [model, models[1]];
    render(<ModelSelector selectedModel={model} onModelSelect={vi.fn()} />);

    expect(
      screen.getByRole('button', { name: 'Select model: Other model' }),
    ).toHaveTextContent('Other model');
  });

  it('shows a single authorized ministry without offering a selector', () => {
    configuration.models = [models[1]];
    render(<ModelSelector selectedModel={null} onModelSelect={vi.fn()} />);

    expect(screen.getByText('MASA')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText('keyboard_arrow_down')).not.toBeInTheDocument();
  });

  it('keeps the pinned ministry visible in chat actions without allowing changes', async () => {
    const user = userEvent.setup();
    render(
      <CunninghamProvider>
        <InputChatActions
          fileUploadEnabled={false}
          webSearchEnabled={false}
          datagouvEnabled={false}
          forceDatagouv={false}
          isUploadingFiles={false}
          isMobile={false}
          forceWebSearch={false}
          onAttachClick={vi.fn()}
          selectedModel={models[3]}
          status={null}
          inputHasContent={false}
        />
      </CunninghamProvider>,
    );

    await user.click(screen.getByText('MSO'));
    expect(
      screen.getByRole('img', { name: 'Ministère MSO' }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: /Select model/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText('MATTE')).not.toBeInTheDocument();
    expect(screen.queryByText('keyboard_arrow_down')).not.toBeInTheDocument();
  });

  it('hides a selected ministry that is no longer authorized', () => {
    configuration.models = [models[1], models[2]];
    const { container } = render(
      <ModelSelector selectedModel={models[0]} onModelSelect={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it('hides the indicator when no models are authorized', () => {
    configuration.models = [];
    const { container } = render(
      <ModelSelector selectedModel={null} onModelSelect={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
