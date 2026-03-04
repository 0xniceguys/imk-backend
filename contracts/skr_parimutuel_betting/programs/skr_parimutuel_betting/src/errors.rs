use anchor_lang::prelude::*;

#[error_code]
pub enum ContractError {
    #[msg("Unauthorized signer or account ownership mismatch.")]
    Unauthorized,
    #[msg("The system is currently paused; bets cannot be placed.")]
    SystemPaused,
    #[msg("The match must be OPEN to perform this action.")]
    MatchNotOpen,
    #[msg("The match must be LOCKED to perform this action.")]
    MatchNotLocked,
    #[msg("The match must be RESOLVED to perform this action.")]
    MatchNotResolved,
    #[msg("The match must be CANCELLED to perform this action.")]
    MatchNotCancelled,
    #[msg("Only OPEN or LOCKED matches can be cancelled.")]
    MatchNotCancellable,
    #[msg("Invalid side selected. Must be A or B.")]
    InvalidSide,
    #[msg("Bet amount is outside the allowed min/max range.")]
    BetOutOfRange,
    #[msg("You have already placed a bet on this match.")]
    AlreadyBet,
    #[msg("You cannot claim a payout because your bet did not win.")]
    NotWinner,
    #[msg("You cannot close this bet because your bet did not lose.")]
    NotLoser,
    #[msg("CRITICAL: Attempted to claim from a match with 0 winning total.")]
    WinningTotalZero,
    #[msg("The provided token account does not match the global SKR mint.")]
    TokenMintMismatch,
    #[msg("The provided vault ATA or vault authority does not match the stored/derived Match PDA values.")]
    VaultAuthorityMismatch,
    #[msg("Fee basis points cannot exceed 1000 (10%).")]
    FeeBpsOutOfRange,
    #[msg("The min_bet cannot be greater than max_bet.")]
    InvalidBetRange,
}
