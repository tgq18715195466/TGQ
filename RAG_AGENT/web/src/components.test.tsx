import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Modal } from './components';

it('keeps the dialog open while its controlled form changes', () => {
  const show = vi.fn(function (this: HTMLDialogElement) { this.open = true; });
  const close = vi.fn(function (this: HTMLDialogElement) { this.open = false; });
  HTMLDialogElement.prototype.showModal = show;
  HTMLDialogElement.prototype.close = close;
  function Form() {
    const [value, setValue] = useState('');
    return <Modal title="反馈" onClose={() => {}}>
      <input aria-label="意见" value={value} onChange={event => setValue(event.target.value)} />
    </Modal>;
  }
  render(<Form />);
  fireEvent.change(screen.getByRole('textbox', { name: '意见' }), { target: { value: '详细意见' } });
  expect(screen.getByRole('textbox', { name: '意见' })).toHaveValue('详细意见');
  expect(show).toHaveBeenCalledTimes(1);
  expect(close).not.toHaveBeenCalled();
});
