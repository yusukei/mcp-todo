/**
 * AgentList — render + select + delete + offline gating.
 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AgentList, { type Agent } from '../../components/workspace/AgentList'

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'a-1',
    name: 'Workstation',
    hostname: 'host-1',
    os_type: 'darwin',
    available_shells: ['/bin/zsh'],
    is_online: true,
    last_seen_at: null,
    created_at: '2026-01-01T00:00:00Z',
    agent_version: '1.2.3',
    ...overrides,
  }
}

describe('AgentList — empty state', () => {
  it('shows the "登録されていません" hint when agents is empty', () => {
    render(
      <AgentList agents={[]} selectedAgentId={null} onSelect={vi.fn()} onDelete={vi.fn()} />,
    )
    expect(screen.getByText(/Agent が登録されていません/)).toBeInTheDocument()
  })
})

describe('AgentList — render', () => {
  it('lists each agent with its name + OS label', () => {
    render(
      <AgentList
        agents={[
          makeAgent({ id: 'a', name: 'Mac Pro', os_type: 'darwin' }),
          makeAgent({ id: 'b', name: 'PC', os_type: 'win32' }),
          makeAgent({ id: 'c', name: 'Server', os_type: 'linux' }),
        ]}
        selectedAgentId={null}
        onSelect={vi.fn()}
        onDelete={vi.fn()}
      />,
    )
    expect(screen.getByText('Mac Pro')).toBeInTheDocument()
    expect(screen.getByText('PC')).toBeInTheDocument()
    expect(screen.getByText('Server')).toBeInTheDocument()
    expect(screen.getByText('macOS')).toBeInTheDocument()
    expect(screen.getByText('Windows')).toBeInTheDocument()
    expect(screen.getByText('Linux')).toBeInTheDocument()
  })
})

describe('AgentList — selection', () => {
  it('clicking an online agent calls onSelect', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <AgentList
        agents={[makeAgent({ id: 'on', is_online: true })]}
        selectedAgentId={null}
        onSelect={onSelect}
        onDelete={vi.fn()}
      />,
    )
    await user.click(screen.getByText('Workstation'))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0].id).toBe('on')
  })

  it('clicking an offline agent still calls onSelect so details can be inspected', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(
      <AgentList
        agents={[makeAgent({ id: 'off', is_online: false })]}
        selectedAgentId={null}
        onSelect={onSelect}
        onDelete={vi.fn()}
      />,
    )
    await user.click(screen.getByText('Workstation'))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect.mock.calls[0][0].id).toBe('off')
  })
})

describe('AgentList — delete button', () => {
  it('clicking the delete affordance calls onDelete (and does not bubble to select)', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    const onDelete = vi.fn()
    render(
      <AgentList
        agents={[makeAgent({ id: 'a-del' })]}
        selectedAgentId={null}
        onSelect={onSelect}
        onDelete={onDelete}
      />,
    )
    await user.click(screen.getByTitle('Agent を削除'))
    expect(onDelete).toHaveBeenCalledWith('a-del')
    expect(onSelect).not.toHaveBeenCalled()
  })
})
