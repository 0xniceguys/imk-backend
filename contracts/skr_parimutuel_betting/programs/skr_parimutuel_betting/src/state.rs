use anchor_lang::prelude::*;

#[account]
pub struct Config {
    pub admin: Pubkey,
    pub skr_mint: Pubkey,
    pub treasury_wallet: Pubkey,
    pub fee_bps: u16,
    pub min_bet: u64,
    pub max_bet: u64,
    pub match_counter: u64,
    pub paused: bool,
}

impl Config {
    pub const SPACE: usize = 8 + 32 + 32 + 32 + 2 + 8 + 8 + 8 + 1 + 7; // 140 bytes -> 168 allocated
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq)]
pub enum MatchStatus {
    Open = 0,
    Locked = 1,
    Resolved = 2,
    Cancelled = 3,
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq)]
pub enum WinnerSide {
    None = 0,
    A = 1,
    B = 2,
}

#[account]
pub struct Match {
    pub id: u64,
    pub status: MatchStatus,
    pub model_a_hash: [u8; 32],
    pub model_b_hash: [u8; 32],
    pub total_a: u64,
    pub total_b: u64,
    pub winner: WinnerSide,
    pub fee_amount: u64,
    pub payout_pool: u64,
    pub winning_total: u64,
    pub claimed_winning_total: u64,
    pub refunded_total: u64,
    pub vault_authority: Pubkey,
    pub vault_ata: Pubkey,
    pub created_at: i64,
    pub locked_at: i64,
    pub resolved_at: i64,
}

impl Match {
    pub const SPACE: usize = 8 + 8 + 1 + 32 + 32 + 8 + 8 + 1 + 8 + 8 + 8 + 8 + 8 + 32 + 32 + 8 + 8 + 8 + 7; // 244 bytes -> 264 allocated
}

#[account]
pub struct UserBet {
    pub match_pubkey: Pubkey,
    pub user: Pubkey,
    pub side: WinnerSide, // Reusing WinnerSide to represent Side A or B
    pub amount: u64,
}

impl UserBet {
    pub const SPACE: usize = 8 + 32 + 32 + 1 + 8 + 7; // 88 bytes -> 96 allocated
}
